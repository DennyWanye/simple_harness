import pytest

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliveryRecord,
    DeliveryStatus,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRecord,
    RunStatus,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.execution.dispatch import dispatch_with_run_fence
from deskpet.execution.fences import (
    TerminalCommitFenceV1,
    UnboundRunExecutionFence,
    UnboundTerminalDeliveryFence,
    validate_run_execution_epoch,
)
from deskpet.harness.kernel import RunKernel
from deskpet.harness.drivers.workflow import WorkflowDriver
from deskpet.harness.ports import DriverStart
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.projector import (
    ExecutionDeliveryDispatcher,
    SinkRegistration,
)


@pytest.mark.asyncio
async def test_unbound_run_fence_is_explicit_and_idempotently_releasable():
    lease = await UnboundRunExecutionFence().acquire("run-1")
    assert lease.run_id == "run-1"
    assert lease.revocation_epoch == 0
    assert lease.released is False
    await lease.release()
    await lease.release()
    assert lease.released is True


@pytest.mark.asyncio
async def test_unbound_terminal_fence_rejects_incomplete_receipt_identity():
    fence = UnboundTerminalDeliveryFence()
    assert await fence.authorize(
        run_id="run-1",
        delivery_id="delivery-1",
        release_receipt_ref="release-1",
    )
    assert not await fence.authorize(
        run_id="run-1",
        delivery_id="",
        release_receipt_ref="release-1",
    )


class _EpochLease:
    def __init__(self, run_id: str, epoch: int) -> None:
        self.run_id = run_id
        self.revocation_epoch = epoch
        self.terminal_commit_fence = None
        self.released = False

    async def release(self) -> None:
        self.released = True


class _EpochFence:
    def __init__(self, epoch: int = 1) -> None:
        self.epoch = epoch
        self.acquire_count = 0

    async def acquire(self, run_id: str) -> _EpochLease:
        self.acquire_count += 1
        return _EpochLease(run_id, self.epoch)


@pytest.mark.asyncio
async def test_effect_completion_body_is_suppressed_after_epoch_drift():
    fence = _EpochFence()
    physical = 0
    delivered = 0

    async def start_effect():
        nonlocal physical
        physical += 1
        return "started"

    _started, captured_epoch = await dispatch_with_run_fence(
        acquire_fence=fence.acquire,
        run_id="run-1",
        operation_kind="tool.dispatch_start",
        operation_id="effect-1",
        invoke=start_effect,
        return_fence_epoch=True,
    )
    fence.epoch += 1
    with pytest.raises(RuntimeError, match="epoch_drift"):
        await validate_run_execution_epoch(
            fence.acquire,
            run_id="run-1",
            expected_epoch=captured_epoch,
        )
    assert physical == 1
    assert delivered == 0


def _durable_record() -> RunRecord:
    capability_hash = fingerprint_json({"tools": []})
    context = RunContext(
        session_id="session-1",
        root_run_id="run-1",
        parent_run_id=None,
        request_id="request-1",
        turn_id="turn-1",
        venue="text",
        workspace={},
        capability_hash=capability_hash,
        provider_plan={"model": "test"},
        trace_id="trace-1",
        principal_id="principal-1",
        auth_epoch=1,
    )
    spec = RunCreate(
        run_id="run-1",
        idempotency_key=root_idempotency_key(
            "session-1", "request-1", "turn-1"
        ),
        context=context,
        payload_fingerprint=fingerprint_json({"text": "hello"}),
        capability_fingerprint=capability_hash,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    )
    return RunRecord(
        spec=spec,
        status=RunStatus.RUNNING,
        persistence_level=PersistenceLevel.DURABLE,
        version=1,
        durable_seq=0,
        terminal_event_id=None,
        created_at=1.0,
        updated_at=1.0,
        started_at=1.0,
    )


@pytest.mark.asyncio
async def test_revoked_terminal_fence_prevents_terminal_uow_body_commit():
    class _DeniedFence:
        async def acquire(self, _run_id: str):
            raise RuntimeError("run_revoked")

    class _Uow:
        commit_count = 0

        async def get_child_command_for_run(self, _run_id):
            return None

        async def read_run_start_snapshot(self, _run_id):
            return None

        async def commit_run_outcome(self, *_args, **_kwargs):
            self.commit_count += 1
            raise AssertionError("terminal writer must remain fenced")

    uow = _Uow()
    kernel = RunKernel(
        uow=uow,
        router=object(),
        drivers={},
        run_execution_fence=_DeniedFence(),
    )
    event = RunEventCandidate(
        event_key="run:final",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload={"text": "must not commit"},
    )

    with pytest.raises(RuntimeError, match="run_revoked"):
        await kernel._finalize(
            _durable_record(),
            expected_version=1,
            status=RunStatus.COMPLETED,
            event=event,
        )
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_terminal_commit_forwards_frozen_fence_and_release_receipt_kind():
    class _StopAfterCapture(RuntimeError):
        pass

    terminal_fence = TerminalCommitFenceV1(
        delivery_fence_epoch=7,
        owner_id="profile-a",
        owner_generation=3,
        dependency_hash="a" * 64,
        snapshot_ref="snapshot-1",
        snapshot_hash="b" * 64,
        release_receipt_kind=(
            "deskpet.capabilities.snapshot_lease_release.v1"
        ),
    )

    class _Lease(_EpochLease):
        def __init__(self):
            super().__init__("run-1", 7)
            self.terminal_commit_fence = terminal_fence

    class _Fence:
        async def acquire(self, _run_id):
            return _Lease()

    class _Uow:
        captured = None

        async def get_child_command_for_run(self, _run_id):
            return None

        async def commit_run_outcome(self, *_args, **kwargs):
            self.captured = kwargs
            raise _StopAfterCapture()

    class _Kernel(RunKernel):
        async def _terminal_deliveries(self, _record):
            from deskpet.execution.contracts import DeliverySpec

            return (
                DeliverySpec(
                    "session_projection",
                    "session",
                    "target-1",
                    DeliveryPolicy.DURABLE_REQUIRED,
                ),
            )

    uow = _Uow()
    kernel = _Kernel(
        uow=uow,
        router=object(),
        drivers={},
        run_execution_fence=_Fence(),
    )
    event = RunEventCandidate(
        event_key="run:final",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload={"text": "final"},
    )

    with pytest.raises(_StopAfterCapture):
        await kernel._finalize(
            _durable_record(),
            expected_version=1,
            status=RunStatus.COMPLETED,
            event=event,
        )
    assert uow.captured["delivery_fence"] == terminal_fence.to_uow_fence()
    assert (
        uow.captured["release_receipt_kind"]
        == terminal_fence.release_receipt_kind
    )


@pytest.mark.asyncio
async def test_revoked_terminal_delivery_discards_before_event_body_read():
    delivery = DeliveryRecord(
        delivery_id="delivery-1",
        event_id="event-1",
        run_id="run-1",
        sink_kind="session_projection",
        sink_instance="session",
        target_id="target-1",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
        status=DeliveryStatus.DELIVERING,
        attempts=1,
        delivery_version=1,
        next_attempt_at=10.0,
        last_error=None,
        created_at=1.0,
        updated_at=1.0,
        delivered_at=None,
    )

    class _Store:
        event_reads = 0
        settled = None

        async def claim_delivery(self, **_kwargs):
            return delivery

        async def get_event(self, _event_id):
            self.event_reads += 1
            raise AssertionError("fenced delivery must not read event body")

        async def settle_delivery(self, _delivery_id, **kwargs):
            self.settled = kwargs

    class _Sink:
        calls = 0

        async def is_bound(self, _target_id):
            return True

        async def deliver(self, _event, _target_id):
            self.calls += 1

    class _TerminalFence:
        async def authorize(self, **_kwargs):
            return False

    store, sink = _Store(), _Sink()
    dispatcher = ExecutionDeliveryDispatcher(
        store,
        (SinkRegistration("session_projection", "session", sink),),
        owner_generation=1,
        terminal_delivery_fence=_TerminalFence(),
    )

    assert await dispatcher.run_once() is True
    assert store.event_reads == 0
    assert sink.calls == 0
    assert store.settled["discard"] is True


@pytest.mark.asyncio
async def test_revoked_workflow_node_fence_prevents_launcher_dispatch():
    class _Launcher:
        launches = 0

        async def launch_precreated(self, **_kwargs):
            self.launches += 1
            raise AssertionError("workflow launcher must remain fenced")

    class _DeniedFence:
        async def acquire(self, _run_id: str):
            raise RuntimeError("workflow_run_revoked")

    profile = ProfileSpec(
        "workflow.fixture",
        "fixture",
        "workflow",
        workflow_key="fixture.v1",
        workflow_name="fixture",
        workflow_version="v1",
        state_factory=dict,
        context_factory=dict,
    )
    profiles = ProfileRegistry((profile,))
    launcher = _Launcher()
    driver = WorkflowDriver(
        launcher,
        object(),
        profiles,
        run_execution_fence_acquirer=_DeniedFence().acquire,
    )
    record = _durable_record()
    start = DriverStart(
        run_id=record.run_id,
        session_id=record.context.session_id,
        canonical_messages=({"role": "user", "content": "run"},),
        run_context=record.context,
        run_spec=record.spec,
        profile_key=profile.profile_key,
        request_payload={"text": "run"},
        capability_snapshot={},
    )

    with pytest.raises(RuntimeError, match="workflow_run_revoked"):
        await anext(driver.start(start))
    assert launcher.launches == 0
