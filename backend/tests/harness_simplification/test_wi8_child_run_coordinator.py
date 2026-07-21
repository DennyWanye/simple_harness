from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    ActorContext,
    AttachmentPolicy,
    AuthorizationError,
    IdempotencyConflict,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    RunStatus,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.harness.child_runs import (
    ChildRunCoordinator,
    ChildRunScheduler as _ChildRunScheduler,
)
from deskpet.harness.ports import (
    AttachmentPolicy as DriverAttachmentPolicy,
    ChildAcceptedSignal,
    ChildTerminalSignal,
    DelegateRun,
    JoinPolicy,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork, StaleRecoveryLease


PARENT_CAPABILITY = fingerprint_json({"tools": ["read", "write", "delegate"]})


class ChildRunScheduler(_ChildRunScheduler):
    """Test helper for exercising both canonical scheduler passes."""

    async def reconcile_once(self, **kwargs) -> None:
        await self.reconcile_commands_once(**kwargs)
        command_errors = self.last_errors
        await self.reconcile_signals_once(
            recovery_lease=kwargs.get("recovery_lease")
        )
        self.last_errors = command_errors + self.last_errors

    @staticmethod
    def _signal(record):
        if record.kind == "accepted":
            return ChildAcceptedSignal(
                record.parent_run_id, record.command_id, record.child_run_id,
                record.signal_id,
            )
        return ChildTerminalSignal(
            record.parent_run_id, record.command_id, record.child_run_id,
            str(record.payload["status"]), record.payload.get("value"),
            record.signal_id,
        )


def _operation_id(parent_run_id: str, command_id: str) -> str:
    return hashlib.sha256(
        f"execution-child-operation|{parent_run_id}|{command_id}".encode()
    ).hexdigest()


def _child_run_id(parent_run_id: str, command_id: str) -> str:
    return "child-" + hashlib.sha256(
        f"execution-child-run|{parent_run_id}|{command_id}".encode()
    ).hexdigest()[:32]


@dataclass
class Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Launcher:
    fail_once: set[str] = field(default_factory=set)
    calls: list[tuple[str, str]] = field(default_factory=list)
    deliveries: list[str] = field(default_factory=list)

    async def accept(self, command) -> None:
        self.calls.append((command.operation_id, command.child_run_id))
        if command.intent.command_id in self.fail_once:
            self.fail_once.remove(command.intent.command_id)
            raise RuntimeError("injected launcher failure")

    async def deliver(self, parent, signal, recovery_lease) -> None:
        self.deliveries.append(signal.signal_id)


def _scheduler(coordinator, launcher, owner):
    return ChildRunScheduler(coordinator, launcher, owner=owner)


async def _pending(coordinator, parent_run_id):
    records = await coordinator._store.list_pending_child_signals(parent_run_id)
    return tuple(
        SimpleNamespace(record=record, signal=ChildRunScheduler._signal(record))
        for record in records
    )


def _parent_spec(run_id: str = "parent-run") -> RunCreate:
    return RunCreate(
        run_id=run_id,
        idempotency_key=root_idempotency_key(
            "session-child", f"request-{run_id}", f"turn-{run_id}"
        ),
        context=RunContext(
            session_id="session-child",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=f"request-{run_id}",
            turn_id=f"turn-{run_id}",
            venue="text",
            workspace={"root": "F:/workspace"},
            capability_hash=PARENT_CAPABILITY,
            provider_plan={"model": "fixture"},
            trace_id=f"trace-{run_id}",
            principal_id="principal-child",
        ),
        payload_fingerprint=fingerprint_json({"prompt": "delegate"}),
        capability_fingerprint=PARENT_CAPABILITY,
        driver_kind="react",
        profile_key="chat",
        persistence_level=PersistenceLevel.DURABLE,
    )


async def _parent(path, clock: Clock, run_id: str = "parent-run"):
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    return (await uow.create(_parent_spec(run_id))).record


@pytest.mark.asyncio
async def test_durable_child_query_rejects_external_actor_from_other_root(tmp_path) -> None:
    clock = Clock()
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    parent = (await uow.create(_parent_spec())).record
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, _delegate("cross-root"))
    await _scheduler(coordinator, Launcher(), "scheduler").reconcile_once()
    assert (await uow.get_child_command(command.operation_id)).status.value == "acked"

    wrong_root = ActorContext(
        principal_id=parent.context.principal_id,
        session_id=parent.context.session_id,
        auth_epoch=parent.context.auth_epoch,
        root_run_id="other-root",
    )
    with pytest.raises(AuthorizationError) as rejected:
        await uow.query(
            RunRef(command.child_run_id, parent.context.session_id), wrong_root
        )
    assert rejected.value.code == "actor_not_authorized"


@pytest.mark.asyncio
async def test_generic_finalize_cannot_bypass_child_parent_signal_owner(tmp_path) -> None:
    clock = Clock()
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    parent = (await uow.create(_parent_spec())).record
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, _delegate("terminal-owner"))
    await _scheduler(coordinator, Launcher(), "scheduler").reconcile_once()
    actor = ActorContext(
        parent.context.principal_id, parent.context.session_id,
        parent.context.auth_epoch, parent.context.root_run_id,
    )
    child = await uow.query(
        RunRef(command.child_run_id, parent.context.session_id), actor
    )

    with pytest.raises(Exception) as rejected:
        await uow.finalize_and_enqueue_delivery(
            command.child_run_id,
            expected_version=child.version,
            terminal_status=RunStatus.COMPLETED,
            event=RunEventCandidate(
                event_key=f"terminal:{command.child_run_id}", kind="final",
                status=OutcomeStatus.SUCCEEDED, driver_kind="react",
            ),
        )
    assert getattr(rejected.value, "code", None) == "child_terminal_requires_parent_signal"
    assert (await uow.query(
        RunRef(command.child_run_id, parent.context.session_id), actor
    )).status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    assert all(
        signal.kind != "terminal"
        for signal in await uow.list_pending_child_signals(parent.run_id)
    )


def _delegate(
    command_id: str,
    join_policy: JoinPolicy = JoinPolicy.JOIN_BEFORE_FINAL,
    *,
    task: str | None = None,
):
    attachment = {
        JoinPolicy.JOIN_BEFORE_FINAL: DriverAttachmentPolicy.ATTACHED,
        JoinPolicy.ROOT_TERMINAL_CHILD: DriverAttachmentPolicy.ROOT_TERMINAL_CHILD,
        JoinPolicy.DETACHED: DriverAttachmentPolicy.DETACHED,
    }[join_policy]
    return DelegateRun(
        run_id="parent-run",
        command_id=command_id,
        child_request={"task": task or command_id, "driver_kind": "react"},
        route_hint="general",
        capability_subset=("read",),
        attachment_policy=attachment,
        join_policy=join_policy,
    )


@pytest.mark.asyncio
async def test_delegate_intent_commits_before_child_exists_and_replays_by_operation(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    coordinator = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    command = _delegate("command-a")

    first = await coordinator.submit(parent, command)
    replay = await coordinator.submit(parent, command)

    assert first == replay
    assert first.operation_id == _operation_id("parent-run", "command-a")
    assert first.child_run_id == _child_run_id("parent-run", "command-a")
    assert first.status.value == "pending"
    assert first.intent.attachment_policy is AttachmentPolicy.ATTACHED
    async with aiosqlite.connect(path) as db:
        child = await (
            await db.execute(
                "SELECT run_id FROM execution_runs WHERE run_id=?",
                (first.child_run_id,),
            )
        ).fetchone()
        command_count = await (
            await db.execute("SELECT COUNT(*) FROM execution_child_commands")
        ).fetchone()
    assert child is None
    assert command_count == (1,)

    with pytest.raises(IdempotencyConflict, match="another intent"):
        await coordinator.submit(
            parent, _delegate("command-a", task="different immutable request")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("join_policy", "attachment"),
    (
        (JoinPolicy.JOIN_BEFORE_FINAL, AttachmentPolicy.ATTACHED),
        (JoinPolicy.ROOT_TERMINAL_CHILD, AttachmentPolicy.ROOT_TERMINAL_CHILD),
        (JoinPolicy.DETACHED, AttachmentPolicy.DETACHED),
    ),
)
async def test_three_join_policies_map_to_existing_attachment_enum_and_link(
    tmp_path, join_policy, attachment
):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    coordinator = ChildRunCoordinator(uow)
    command = _delegate(f"command-{join_policy.value}", join_policy)
    committed = await coordinator.submit(parent, command)

    await _scheduler(coordinator, Launcher(), "scheduler-a").reconcile_once(
        lease_seconds=30
    )

    assert (await uow.get_child_command(committed.operation_id)).status.value == "acked"
    assert committed.intent.attachment_policy is attachment
    async with aiosqlite.connect(path) as db:
        link = await (
            await db.execute(
                """SELECT parent_run_id,child_run_id,attachment_policy,link_kind
                FROM execution_run_links WHERE child_run_id=?""",
                (committed.child_run_id,),
            )
        ).fetchone()
    assert link == (
        "parent-run",
        committed.child_run_id,
        attachment.value,
        "structural",
    )


@pytest.mark.asyncio
async def test_scheduler_state_machine_is_pending_leased_scheduled_acked(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    coordinator = ChildRunCoordinator(uow)
    pending = await coordinator.submit(parent, _delegate("state-machine"))
    assert pending.status.value == "pending"

    leased = (await uow.lease_child_commands(
        owner="scheduler-a", limit=1, lease_seconds=30
    ))[0]
    assert leased.status.value == "leased"
    scheduled = await uow.schedule_child_command(
        leased.operation_id,
        lease_owner="scheduler-a",
        lease_epoch=leased.schedule_lease_epoch,
    )
    assert scheduled.status.value == "scheduled"
    acked = await uow.acknowledge_child_command(
        scheduled.operation_id,
        lease_owner="scheduler-a",
        lease_epoch=scheduled.schedule_lease_epoch,
    )
    assert acked.status.value == "acked"


@pytest.mark.asyncio
async def test_commit_crash_rolls_back_command_and_never_creates_child(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)

    def fault(point: str) -> None:
        if point == "child_command_before_commit":
            raise RuntimeError("crash before delegate commit")

    coordinator = ChildRunCoordinator(
        SqliteExecutionUnitOfWork(path, clock=clock, fault_injector=fault)
    )
    with pytest.raises(RuntimeError, match="delegate commit"):
        await coordinator.submit(parent, _delegate("crash-commit"))

    async with aiosqlite.connect(path) as db:
        commands = await (
            await db.execute("SELECT COUNT(*) FROM execution_child_commands")
        ).fetchone()
        runs = await (await db.execute("SELECT COUNT(*) FROM execution_runs")).fetchone()
    assert commands == (0,)
    assert runs == (1,)


@pytest.mark.asyncio
async def test_schedule_crash_rolls_back_child_and_link_then_expired_lease_recovers(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    healthy = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    committed = await healthy.submit(parent, _delegate("crash-schedule"))

    def fault(point: str) -> None:
        if point == "child_schedule_after_run":
            raise RuntimeError("crash after child insert")

    crashing = ChildRunCoordinator(
        SqliteExecutionUnitOfWork(path, clock=clock, fault_injector=fault)
    )
    failed = _scheduler(crashing, Launcher(), "scheduler-crash")
    await failed.reconcile_once(lease_seconds=10)
    assert any("child insert" in error for error in failed.last_errors)

    async with aiosqlite.connect(path) as db:
        child = await (
            await db.execute(
                "SELECT run_id FROM execution_runs WHERE run_id=?",
                (committed.child_run_id,),
            )
        ).fetchone()
        link = await (
            await db.execute(
                "SELECT child_run_id FROM execution_run_links WHERE child_run_id=?",
                (committed.child_run_id,),
            )
        ).fetchone()
    assert child is None
    assert link is None

    clock.advance(11)
    launcher = Launcher()
    await _scheduler(healthy, launcher, "scheduler-recovery").reconcile_once(
        lease_seconds=10
    )
    assert launcher.calls == [(committed.operation_id, committed.child_run_id)]


@pytest.mark.asyncio
async def test_ack_crash_reuses_same_child_and_operation_without_duplicate_rows(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    healthy = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    committed = await healthy.submit(parent, _delegate("crash-ack"))

    def fault(point: str) -> None:
        if point == "child_ack_before_commit":
            raise RuntimeError("crash before accepted inbox commit")

    launcher = Launcher()
    crashing = ChildRunCoordinator(
        SqliteExecutionUnitOfWork(path, clock=clock, fault_injector=fault)
    )
    failed = _scheduler(crashing, launcher, "scheduler-crash")
    await failed.reconcile_once(lease_seconds=10)
    assert any("accepted inbox" in error for error in failed.last_errors)

    stored = await SqliteExecutionUnitOfWork(path, clock=clock).get_child_command(
        committed.operation_id
    )
    assert stored is not None and stored.status.value == "scheduled"
    assert await _pending(healthy, "parent-run") == ()

    clock.advance(11)
    await _scheduler(healthy, launcher, "scheduler-recovery").reconcile_once(
        lease_seconds=10
    )
    assert launcher.calls == [
        (committed.operation_id, committed.child_run_id),
        (committed.operation_id, committed.child_run_id),
    ]
    async with aiosqlite.connect(path) as db:
        run_count = await (
            await db.execute(
                "SELECT COUNT(*) FROM execution_runs WHERE run_id=?",
                (committed.child_run_id,),
            )
        ).fetchone()
        link_count = await (
            await db.execute(
                "SELECT COUNT(*) FROM execution_run_links WHERE child_run_id=?",
                (committed.child_run_id,),
            )
        ).fetchone()
    assert run_count == (1,)
    assert link_count == (1,)


@pytest.mark.asyncio
async def test_one_command_ack_failure_does_not_block_sibling_schedule(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    healthy = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    first = await healthy.submit(parent, _delegate("first"))
    second = await healthy.submit(parent, _delegate("second"))
    fail_next_ack = True

    def fault(point: str) -> None:
        nonlocal fail_next_ack
        if point == "child_ack_before_commit" and fail_next_ack:
            fail_next_ack = False
            raise RuntimeError("first ack failed")

    coordinator = ChildRunCoordinator(
        SqliteExecutionUnitOfWork(path, clock=clock, fault_injector=fault)
    )
    scheduler = _scheduler(coordinator, Launcher(), "scheduler-isolated")
    await scheduler.reconcile_commands_once(limit=2, lease_seconds=10)

    assert any("first ack failed" in error for error in scheduler.last_errors)
    stored = [
        await SqliteExecutionUnitOfWork(path, clock=clock).get_child_command(operation_id)
        for operation_id in (first.operation_id, second.operation_id)
    ]
    assert sorted(item.status.value for item in stored) == ["acked", "scheduled"]
    acked = next(item for item in stored if item.status.value == "acked")
    signals = await _pending(healthy, parent.run_id)
    assert [item.record.operation_id for item in signals] == [acked.operation_id]


@pytest.mark.asyncio
async def test_accepted_and_terminal_inbox_redeliver_after_restart_until_ack(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    first = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    committed = await first.submit(parent, _delegate("signals"))
    await _scheduler(first, Launcher(), "scheduler-a").reconcile_once()

    restarted = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    accepted_once = await _pending(restarted, "parent-run")
    accepted_twice = await _pending(restarted, "parent-run")
    assert len(accepted_once) == len(accepted_twice) == 1
    assert accepted_once[0].signal.kind == "child_accepted"
    assert accepted_once[0].record.signal_id == accepted_twice[0].record.signal_id
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    await uow.save_continuation("parent-run", 0, {"state": "waiting"})
    applied = await uow.apply_child_signal_and_ack(
        accepted_once[0].record.signal_id,
        expected_continuation_version=1,
        continuation_payload={"state": "accepted"},
        event=RunEventCandidate(
            event_key="accepted-applied", kind="child.accepted",
            status=OutcomeStatus.ACCEPTED, driver_kind="react",
        ),
    )
    assert await _pending(restarted, "parent-run") == ()
    advanced = await uow.save_continuation("parent-run", 2, {"state": "advanced"})
    assert advanced.version == 3
    replayed = await SqliteExecutionUnitOfWork(path, clock=clock).apply_child_signal_and_ack(
        accepted_once[0].record.signal_id,
        expected_continuation_version=1,
        continuation_payload={"state": "accepted"},
        event=RunEventCandidate(
            event_key="accepted-applied", kind="child.accepted",
            status=OutcomeStatus.ACCEPTED, driver_kind="react",
        ),
    )
    assert replayed == applied
    with pytest.raises(IdempotencyConflict):
        await uow.apply_child_signal_and_ack(
            accepted_once[0].record.signal_id,
            expected_continuation_version=1,
            continuation_payload={"state": "foreign"},
            event=RunEventCandidate(
                event_key="accepted-applied", kind="child.accepted",
                status=OutcomeStatus.ACCEPTED, driver_kind="react",
            ),
        )

    await uow.finalize_child_and_enqueue_parent_signal(
        committed.operation_id,
        expected_version=1,
        terminal_status="completed",
        event=RunEventCandidate(
            event_key="child-terminal",
            kind="run.completed",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
            payload={"result": "ok"},
        ),
        value={"result": "ok"},
    )
    after_terminal_restart = ChildRunCoordinator(
        SqliteExecutionUnitOfWork(path, clock=clock)
    )
    terminal = await _pending(after_terminal_restart, "parent-run")
    assert len(terminal) == 1
    assert terminal[0].signal == ChildTerminalSignal(
        "parent-run", "signals", committed.child_run_id, "completed", {"result": "ok"}
        , terminal[0].record.signal_id
    )


@pytest.mark.asyncio
async def test_signal_ack_crash_keeps_inbox_pending_for_recovery(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    healthy = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    await healthy.submit(parent, _delegate("signal-ack-crash"))
    await _scheduler(healthy, Launcher(), "scheduler-a").reconcile_once()
    delivery = (await _pending(healthy, "parent-run"))[0]

    def fault(point: str) -> None:
        if point == "child_signal_ack_before_commit":
            raise RuntimeError("crash before signal ack")

    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    await uow.save_continuation("parent-run", 0, {"state": "waiting"})
    kwargs = dict(
        expected_continuation_version=1,
        continuation_payload={"state": "accepted"},
        event=RunEventCandidate(
            event_key="accepted-after-crash", kind="child.accepted",
            status=OutcomeStatus.ACCEPTED, driver_kind="react",
        ),
    )
    crashing = SqliteExecutionUnitOfWork(
        path, clock=clock, fault_injector=fault
    )
    with pytest.raises(RuntimeError, match="signal ack"):
        await crashing.apply_child_signal_and_ack(delivery.record.signal_id, **kwargs)

    redelivered = await _pending(healthy, "parent-run")
    assert [item.record.signal_id for item in redelivered] == [delivery.record.signal_id]
    await uow.apply_child_signal_and_ack(delivery.record.signal_id, **kwargs)


@pytest.mark.asyncio
async def test_stale_recovery_cannot_apply_child_boundary_event_or_ack(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    coordinator = ChildRunCoordinator(uow)
    await coordinator.submit(parent, _delegate("stale-signal"))
    await _scheduler(coordinator, Launcher(), "scheduler-a").reconcile_once()
    delivery = (await _pending(coordinator, "parent-run"))[0]
    stale = await uow.claim_recovery("parent-run", owner="worker-a", lease_seconds=5)
    clock.advance(6)
    await uow.claim_recovery("parent-run", owner="worker-b", lease_seconds=10)

    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await uow.list_pending_child_signals(
            "parent-run", recovery_lease=stale
        )
    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await uow.apply_child_signal_and_ack(
            delivery.record.signal_id,
            expected_continuation_version=0,
            continuation_payload={"children": [delivery.record.child_run_id]},
            event=RunEventCandidate(
                event_key="child-applied",
                kind="child.accepted",
                status=OutcomeStatus.ACCEPTED,
                driver_kind="react",
            ),
            recovery_lease=stale,
        )

    assert await uow.load_continuation("parent-run") is None
    assert await uow.list_events("parent-run") == ()
    assert [item.record.signal_id for item in await _pending(coordinator, "parent-run")] == [
        delivery.record.signal_id
    ]


@pytest.mark.asyncio
async def test_launcher_failure_isolated_between_siblings_and_retry_is_idempotent(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    coordinator = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    first = await coordinator.submit(parent, _delegate("sibling-a"))
    second = await coordinator.submit(parent, _delegate("sibling-b"))
    launcher = Launcher(fail_once={"sibling-a"})

    scheduler = _scheduler(coordinator, launcher, "scheduler-a")
    await scheduler.reconcile_once(
        limit=10, lease_seconds=10
    )

    assert any("injected launcher failure" in error for error in scheduler.last_errors)
    assert (await coordinator._store.get_child_command(first.operation_id)).status.value == "scheduled"
    assert (await coordinator._store.get_child_command(second.operation_id)).status.value == "acked"
    assert (await _pending(coordinator, "parent-run"))[0].signal == (
        ChildAcceptedSignal(
            "parent-run", "sibling-b", second.child_run_id,
            (await _pending(coordinator, "parent-run"))[0].record.signal_id,
        )
    )

    clock.advance(11)
    await _scheduler(coordinator, launcher, "scheduler-b").reconcile_once(
        limit=10, lease_seconds=10
    )
    assert (await coordinator._store.get_child_command(first.operation_id)).status.value == "acked"
    assert launcher.calls.count((first.operation_id, first.child_run_id)) == 2
    async with aiosqlite.connect(path) as db:
        children = await (
            await db.execute(
                """SELECT parent_run_id,COUNT(*) FROM execution_run_links
                WHERE parent_run_id='parent-run' GROUP BY parent_run_id"""
            )
        ).fetchone()
    assert children == ("parent-run", 2)


@pytest.mark.asyncio
async def test_detached_parent_terminal_discards_replayed_child_signals(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(
        parent, _delegate("detached-terminal", JoinPolicy.DETACHED)
    )
    launcher = Launcher()
    scheduler = ChildRunScheduler(coordinator, launcher, owner="scheduler-a")
    await scheduler.reconcile_once()
    first_deliveries = tuple(launcher.deliveries)

    await uow.finalize_and_enqueue_delivery(
        parent.run_id,
        expected_version=parent.version,
        terminal_status=RunStatus.COMPLETED,
        event=RunEventCandidate(
            event_key="parent-first",
            kind="final",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
        ),
    )
    await uow.finalize_child_and_enqueue_parent_signal(
        command.operation_id,
        expected_version=1,
        terminal_status=RunStatus.COMPLETED,
        event=RunEventCandidate(
            event_key="child-late",
            kind="final",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
        ),
        value={"result": "late"},
    )

    await scheduler.reconcile_once()

    assert tuple(launcher.deliveries) == first_deliveries
    assert await _pending(coordinator, parent.run_id) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "join_policy", [JoinPolicy.JOIN_BEFORE_FINAL, JoinPolicy.ROOT_TERMINAL_CHILD]
)
async def test_terminal_parent_preserves_attached_signal_as_invariant(join_policy, tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    coordinator = ChildRunCoordinator(uow)
    await coordinator.submit(parent, _delegate("attached-terminal", join_policy))
    scheduler = ChildRunScheduler(coordinator, Launcher(), owner="scheduler-a")
    await scheduler.reconcile_once()
    await uow.finalize_and_enqueue_delivery(
        parent.run_id, expected_version=parent.version,
        terminal_status=RunStatus.COMPLETED,
        event=RunEventCandidate(
            event_key="parent-race", kind="final",
            status=OutcomeStatus.SUCCEEDED, driver_kind="react",
        ),
    )

    await scheduler.reconcile_signals_once()

    assert await _pending(coordinator, parent.run_id)
    assert any("attached_child_signal_after_parent_terminal" in item
               for item in scheduler.last_errors)


@pytest.mark.asyncio
async def test_scheduler_lifecycle_wakes_for_new_command_and_closes(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    coordinator = ChildRunCoordinator(uow)
    launcher = Launcher()
    scheduler = ChildRunScheduler(coordinator, launcher, owner="runtime-owner")
    await scheduler.start(interval=60)

    command = await coordinator.submit(parent, _delegate("wake-command"))
    for _ in range(100):
        current = await uow.get_child_command(command.operation_id)
        if current is not None and current.status.value == "acked":
            break
        await asyncio.sleep(0.01)

    assert current is not None and current.status.value == "acked"
    assert scheduler._task is not None and not scheduler._task.done()
    await scheduler.close()
    assert scheduler._task is None


def test_owner_audit_uses_run_driver_and_structural_link_without_owner_map():
    from pathlib import Path

    source = Path("backend/deskpet/harness/child_runs.py").read_text(encoding="utf-8")
    assert "owner_map" not in source
    assert "_owners" not in source
    assert 'driver_kind=str(child_request.get("driver_kind") or "react")' in source
    assert "commit_child_command" in source
    assert "attachment_policy=attachment" in source
