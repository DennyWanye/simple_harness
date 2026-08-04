from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    PersistenceLevel,
    RecoveryLease,
    RunContext,
    RunCreate,
    RunRef,
    RunStatus,
)
from deskpet.harness.bootstrap import HarnessRuntime, build_harness_runtime
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.kernel import HostContext, RegisteredDriver
from deskpet.harness.ports import (
    DriverRecoveryDeferred,
    DriverTerminalCandidate,
)
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute
from deskpet.harness.reconciler import HarnessReconciler
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


def _host(session_id: str) -> HostContext:
    return HostContext(
        session_id=session_id,
        principal_id=f"principal-{session_id}",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("primary",),
        trace_id="trace-1",
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
    )

    assert dict(runtime.manifest) == {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "operations": ["start", "observe", "signal", "cancel", "recover", "close"],
        "drivers": ["react"],
        "profiles": [{"key": "react.default", "driver_kind": "react"}],
        "event_contract": "execution.run-event.v1",
        "tool_stage": "prepared-call.v1",
    }
    assert dict(runtime.health) == {
        "status": "degraded",
        "active_owner": "sqlite_execution_uow",
        "ledger_schema_version": WORKFLOW_SCHEMA_VERSION,
        "drivers": {"react": "ready"},
        "degraded_reasons": [
            "tool_executor_unavailable",
            "child_run_coordinator_unavailable",
        ],
    }

    handle = await runtime.run_client.start(
        {"text": "hello", "request_id": "request-1", "turn_id": "turn-1"},
        _host("session-1"),
    )
    events = [event async for event in handle.events]
    assert events[-1].candidate.payload["text"] == "ok"

    second = await runtime.run_client.start(
        {"text": "late", "request_id": "request-2", "turn_id": "turn-2"},
        _host("session-1"),
    )
    await asyncio.sleep(0.02)
    late_events = [event async for event in second.events]
    assert late_events[-1].kind == "run.final"
    await runtime.close()


@pytest.mark.asyncio
async def test_bootstrap_uses_event_reconciliation_without_resident_owner(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    driver = Driver()
    runtime = await build_harness_runtime(
        uow=uow,
        classifier=Classifier(),
        profiles=profiles(),
        drivers=[RegisteredDriver("react", driver)],
        child_runs=ChildRunCoordinator(uow),
    )

    assert runtime.reconciler._kernel is runtime.kernel
    assert not hasattr(runtime.reconciler, "_task")
    await runtime.close()
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

    class Reconciler:
        async def close(self, timeout):
            timeline.append("reconciler-drain")
            return True

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
        Kernel(), None, None, None, Reconciler(),
        (RegisteredDriver("react", ClosingDriver()),), effects,
    )

    await runtime.close(timeout=0.1)

    assert timeline == [
        "reconciler-drain", "drain", "final-recover", "kernel-drain",
        "driver-close", "live-finish",
    ]


@pytest.mark.asyncio
async def test_event_trigger_coalesces_burst_into_one_transient_worker() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    passes = 0

    class Uow:
        async def list_recoverable(self, **kwargs):
            return ()

    reconciler = HarnessReconciler(Uow(), object())

    async def blocked_pass() -> None:
        nonlocal passes
        passes += 1
        entered.set()
        await release.wait()

    reconciler.reconcile_all = blocked_pass
    reconciler.trigger()
    await entered.wait()
    for _ in range(500):
        reconciler.trigger()

    workers = [
        task for task in asyncio.all_tasks()
        if task.get_name() == "harness-reconcile-event"
    ]
    assert len(workers) == 1
    release.set()
    assert await reconciler.drain(0.2)
    assert passes == 2


@pytest.mark.asyncio
async def test_event_worker_drains_all_immediate_deliveries() -> None:
    class Uow:
        async def list_recoverable(self, **kwargs):
            return ()

    class Delivery:
        remaining = 3
        calls = 0

        async def run_once(self) -> bool:
            self.calls += 1
            if self.remaining == 0:
                return False
            self.remaining -= 1
            return True

    delivery = Delivery()
    reconciler = HarnessReconciler(Uow(), object(), delivery=delivery)
    reconciler.trigger()

    assert await reconciler.drain(0.2)
    assert delivery.remaining == 0
    assert delivery.calls == 4


@pytest.mark.asyncio
async def test_startup_reconciliation_drains_more_than_one_command_page() -> None:
    commands = [
        SimpleNamespace(operation_id=f"operation-{index}", schedule_lease_epoch=1)
        for index in range(33)
    ]

    class Uow:
        lease_calls = 0

        async def lease_child_commands(self, *, limit, **kwargs):
            self.lease_calls += 1
            leased = tuple(commands[:limit])
            del commands[:limit]
            return leased

        async def schedule_child_command(self, operation_id, **kwargs):
            return SimpleNamespace(
                operation_id=operation_id, schedule_lease_epoch=1)

        async def acknowledge_child_command(self, *args, **kwargs):
            return None

        async def list_pending_child_signal_parents(self, **kwargs):
            return ()

        async def list_recoverable(self, **kwargs):
            return ()

    class Live:
        _runs = {}

        def values(self):
            return ()

    class Kernel:
        _live = Live()
        accepted: list[str] = []

        async def _accept_precreated_child(self, command):
            self.accepted.append(command.operation_id)

    uow, kernel = Uow(), Kernel()
    reconciler = HarnessReconciler(
        uow, kernel, coordinator=object(), batch_limit=16)
    await reconciler._reconcile_startup()

    assert len(kernel.accepted) == 33
    assert uow.lease_calls == 3
    assert commands == []


@pytest.mark.asyncio
async def test_runtime_close_cancels_reconciliation_before_driver_close() -> None:
    entered = asyncio.Event()
    timeline: list[str] = []

    class Uow:
        async def list_recoverable(self, **kwargs):
            return ()

    class Live:
        def finish_all(self):
            timeline.append("live-finish")

    class Kernel:
        _lock = asyncio.Lock()
        _live = Live()

        async def _drain_active(self, timeout):
            timeline.append("kernel-drain")

    class ClosingDriver(Driver):
        async def close(self):
            assert not any(
                task.get_name() == "harness-reconcile-event"
                for task in asyncio.all_tasks()
            )
            timeline.append("driver-close")

    reconciler = HarnessReconciler(Uow(), object())

    async def blocked_pass() -> None:
        entered.set()
        await asyncio.Event().wait()

    reconciler.reconcile_all = blocked_pass
    reconciler.trigger()
    await entered.wait()
    runtime = HarnessRuntime(
        Kernel(), None, None, None, reconciler,
        (RegisteredDriver("react", ClosingDriver()),), None,
    )
    await runtime.close(timeout=0.02)

    assert timeline == ["kernel-drain", "driver-close", "live-finish"]
    assert not any(
        task.get_name() == "harness-reconcile-event"
        for task in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_event_trigger_advances_every_bounded_lane_without_resident_task() -> None:
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

    coordinator = object()
    uow, delivery = Uow(), Delivery()
    reconciler = HarnessReconciler(
        uow, object(), Effects(), coordinator=coordinator,
        delivery=delivery,
        item_timeout=0.05, batch_limit=16,
    )
    reconciler.trigger()
    assert await reconciler.drain(0.2)

    assert not hasattr(reconciler, "_task")
    assert uow.calls[:4] == [
        ("commands", 16), ("signals", 16),
        ("recovery", 17), ("recovery", 17),
    ]
    assert uow.recovery_run_ids[:2] == [(), ("late-run",)]
    assert delivery.calls == 1


@pytest.mark.asyncio
async def test_reconciler_timeout_records_error_and_yields_to_later_lanes() -> None:
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
    coordinator = object()
    reconciler = HarnessReconciler(
        uow, object(), coordinator=coordinator,
        item_timeout=0.005,
    )
    await reconciler.reconcile_all()

    assert uow.signals == 1
    assert uow.recovery == 1
    assert reconciler.last_errors
    assert reconciler.last_errors[0].startswith("child_commands:TimeoutError:")


@pytest.mark.asyncio
async def test_effect_ready_recovery_does_not_wait_for_or_cancel_live_owner() -> None:
    release = asyncio.Event()
    owner = asyncio.create_task(release.wait(), name="live-provider-owner")
    actor = _host("session-live").actor(root_run_id="run-live")
    record = SimpleNamespace(
        run_id="run-live", context=SimpleNamespace(actor=lambda: actor)
    )
    active = SimpleNamespace(task=owner, recovery_deferred_until=0.0)

    class Live:
        _runs = {"run-live": active}

        def get(self, run_id):
            return self._runs.get(run_id)

    class Uow:
        async def list_recoverable(self, **kwargs):
            return (record,)

    class Kernel:
        _live = Live()
        recover_calls = 0

        async def recover(self, ref, supplied_actor):
            self.recover_calls += 1

    class Effects:
        @staticmethod
        def ready_run_ids():
            return frozenset({"run-live"})

    kernel = Kernel()
    reconciler = HarnessReconciler(
        Uow(), kernel, Effects(), item_timeout=0.005
    )
    await reconciler.reconcile_all()

    assert reconciler.last_errors == ()
    assert kernel.recover_calls == 0
    assert not owner.done()
    await reconciler.close(0.1)
    release.set()
    await owner


@pytest.mark.asyncio
async def test_reconciler_budget_does_not_cancel_slow_child_signal_resume() -> None:
    parent = SimpleNamespace(run_id="parent", status=RunStatus.RUNNING)
    signal = SimpleNamespace(
        signal_id="signal",
        parent_run_id="parent",
        command_id="command",
        child_run_id="child",
        kind="terminal",
        payload={"status": "completed", "value": "done"},
        delivered_at=None,
    )
    completed = asyncio.Event()
    background: list[asyncio.Task[None]] = []

    class Uow:
        async def lease_child_commands(self, **kwargs):
            return ()

        async def list_pending_child_signal_parents(self, **kwargs):
            return (parent,)

        async def list_pending_child_signals(self, *args, **kwargs):
            return (signal,)

        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if isinstance(subject, str):
                return RecoveryLease(subject, owner or "test", 1, 9999)
            return subject

        async def list_recoverable(self, **kwargs):
            return ()

    async def deliver(*_args):
        async def resume() -> None:
            await asyncio.sleep(0.03)
            completed.set()

        background.append(asyncio.create_task(resume()))
        return True

    reconciler = HarnessReconciler(
        Uow(),
        SimpleNamespace(_deliver_child_signal=deliver),
        coordinator=object(),
        item_timeout=0.005,
    )

    await reconciler.reconcile_all()
    await asyncio.wait_for(completed.wait(), timeout=0.2)

    assert not hasattr(reconciler, "_task")
    await asyncio.gather(*background)


@pytest.mark.asyncio
async def test_reconciler_does_not_touch_child_inboxes_without_coordinator() -> None:
    class Uow:
        calls = 0

        async def lease_child_commands(self, **kwargs):
            self.calls += 1
            return ()

        async def list_pending_child_signal_parents(self, **kwargs):
            self.calls += 1
            return ()

    uow = Uow()
    reconciler = HarnessReconciler(uow, object())
    await reconciler.reconcile_commands_once()
    await reconciler.reconcile_signals_once()
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
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_lane", ("child_commands", "child_signals"))
async def test_bootstrap_one_shot_lane_error_fails_before_ingress_opens(
    tmp_path, monkeypatch, failed_lane: str
) -> None:
    async def commands(self, **kwargs):
        self.last_errors = (
            ("operation:RuntimeError::boom",) if failed_lane == "child_commands" else ()
        )

    async def signals(self, **kwargs):
        self.last_errors = (
            ("signal:RuntimeError::boom",) if failed_lane == "child_signals" else ()
        )

    monkeypatch.setattr(HarnessReconciler, "reconcile_commands_once", commands)
    monkeypatch.setattr(HarnessReconciler, "reconcile_signals_once", signals)

    driver = Driver()
    with pytest.raises(RuntimeError, match=failed_lane):
        await build_harness_runtime(
            uow=SqliteExecutionUnitOfWork(tmp_path / f"{failed_lane}.db"),
            classifier=Classifier(),
            profiles=profiles(),
            drivers=[RegisteredDriver("react", driver)],
        )

    assert driver.closed is True


@pytest.mark.asyncio
async def test_bootstrap_recovers_current_generation_before_activated_opens(tmp_path) -> None:
    path = tmp_path / "activated-bootstrap.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.activate_runtime()
    context = RunContext(
        session_id="session-1",
        root_run_id="run-activated-recover",
        parent_run_id=None,
        request_id="request-activated-recover",
        turn_id="turn-activated-recover",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-activated-recover",
        principal_id="principal-session-1",
        auth_epoch=1,
    )
    await uow.create(RunCreate(
        run_id="run-activated-recover",
        idempotency_key="root:activated-recover",
        context=context,
        payload_fingerprint="a" * 64,
        capability_fingerprint="c" * 64,
        driver_kind="react",
        profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    ))
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE execution_runtime_state SET phase='activated',updated_at=100.0"
        )
        await db.commit()

    driver = Driver()
    runtime = await build_harness_runtime(
        uow=uow,
        classifier=Classifier(),
        profiles=profiles(),
        drivers=[RegisteredDriver("react", driver)],
    )
    active = runtime.kernel._live.get("run-activated-recover")
    assert active is not None
    await active.task

    assert driver.recovered == ["run-activated-recover"]
    assert (await uow.get_runtime_state()).phase == "activated"
    await runtime.close()


@pytest.mark.asyncio
async def test_bootstrap_fails_before_open_when_recovery_preparation_fails(tmp_path) -> None:
    path = tmp_path / "activated-bootstrap-failure.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.activate_runtime()
    context = RunContext(
        session_id="session-1", root_run_id="run-recovery-failure",
        parent_run_id=None, request_id="request-recovery-failure",
        turn_id="turn-recovery-failure", venue="text", workspace={},
        capability_hash="c" * 64, provider_plan={},
        trace_id="trace-recovery-failure", principal_id="principal-session-1",
        auth_epoch=1,
    )
    await uow.create(RunCreate(
        run_id="run-recovery-failure", idempotency_key="root:recovery-failure",
        context=context, payload_fingerprint="a" * 64,
        capability_fingerprint="c" * 64, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    ))
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE execution_runtime_state SET phase='activated',updated_at=100.0"
        )
        await db.commit()

    class BrokenRecoveryDriver(Driver):
        async def prepare_recovery(self, run_id, recovery_lease):
            raise RuntimeError("trusted recovery context unavailable")

    driver = BrokenRecoveryDriver()
    with pytest.raises(RuntimeError, match="trusted recovery context unavailable"):
        await build_harness_runtime(
            uow=uow, classifier=Classifier(), profiles=profiles(),
            drivers=[RegisteredDriver("react", driver)],
        )

    assert driver.closed is True


@pytest.mark.asyncio
async def test_bootstrap_stays_open_when_recovery_is_durably_deferred(
    tmp_path,
) -> None:
    path = tmp_path / "activated-bootstrap-deferred.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.activate_runtime()
    context = RunContext(
        session_id="session-1", root_run_id="run-recovery-deferred",
        parent_run_id=None, request_id="request-recovery-deferred",
        turn_id="turn-recovery-deferred", venue="text", workspace={},
        capability_hash="c" * 64, provider_plan={},
        trace_id="trace-recovery-deferred", principal_id="principal-session-1",
        auth_epoch=1,
    )
    await uow.create(RunCreate(
        run_id="run-recovery-deferred", idempotency_key="root:recovery-deferred",
        context=context, payload_fingerprint="a" * 64,
        capability_fingerprint="c" * 64, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.WAITING,
    ))

    class DeferredRecoveryDriver(Driver):
        def __init__(self) -> None:
            super().__init__()
            self.prepares = 0

        async def prepare_recovery(self, run_id, recovery_lease):
            self.prepares += 1
            raise DriverRecoveryDeferred("waiting for user decision")

    driver = DeferredRecoveryDriver()
    runtime = await build_harness_runtime(
        uow=uow, classifier=Classifier(), profiles=profiles(),
        drivers=[RegisteredDriver("react", driver)],
    )

    assert driver.prepares == 1
    assert (await uow.query(
        RunRef("run-recovery-deferred", "session-1"),
        context.actor(),
    )).status is RunStatus.WAITING
    await runtime.reconciler.reconcile_all()
    assert driver.prepares == 1
    await runtime.close()


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
    )

    active = runtime.kernel._live.get("run-recover")
    assert active is not None
    await active.task

    assert driver.recovered == ["run-recover"]
    await runtime.close()
