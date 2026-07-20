from __future__ import annotations

from dataclasses import dataclass, field

import aiosqlite
import pytest

from deskpet.execution import (
    AttachmentPolicy,
    IdempotencyConflict,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.harness.child_runs import (
    ChildRunCoordinator,
    attachment_for_join_policy,
    join_policy_for_attachment,
    stable_child_operation_id,
    stable_child_run_id,
)
from deskpet.harness.ports import (
    AttachmentPolicy as DriverAttachmentPolicy,
    ChildAcceptedSignal,
    ChildTerminalSignal,
    DelegateRun,
    JoinPolicy,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork


PARENT_CAPABILITY = fingerprint_json({"tools": ["read", "write", "delegate"]})


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

    async def accept(self, command) -> None:
        self.calls.append((command.operation_id, command.child_run_id))
        if command.intent.command_id in self.fail_once:
            self.fail_once.remove(command.intent.command_id)
            raise RuntimeError("injected launcher failure")


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


def _delegate(
    command_id: str,
    join_policy: JoinPolicy = JoinPolicy.JOIN_BEFORE_FINAL,
    *,
    task: str | None = None,
) -> DelegateRun:
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
    assert first.operation_id == stable_child_operation_id("parent-run", "command-a")
    assert first.child_run_id == stable_child_run_id("parent-run", "command-a")
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

    attempts = await coordinator.run_scheduler_once(
        Launcher(), owner="scheduler-a", lease_seconds=30
    )

    assert len(attempts) == 1
    assert attempts[0].accepted is True
    assert attempts[0].command.status.value == "acked"
    assert attachment_for_join_policy(join_policy) is attachment
    assert join_policy_for_attachment(attachment) is join_policy
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
    with pytest.raises(RuntimeError, match="child insert"):
        await crashing.run_scheduler_once(
            Launcher(), owner="scheduler-crash", lease_seconds=10
        )

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
    recovered = await healthy.run_scheduler_once(
        launcher, owner="scheduler-recovery", lease_seconds=10
    )
    assert recovered[0].accepted is True
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
    with pytest.raises(RuntimeError, match="accepted inbox"):
        await crashing.run_scheduler_once(
            launcher, owner="scheduler-crash", lease_seconds=10
        )

    stored = await SqliteExecutionUnitOfWork(path, clock=clock).get_child_command(
        committed.operation_id
    )
    assert stored is not None and stored.status.value == "scheduled"
    assert await healthy.pending_signals("parent-run") == ()

    clock.advance(11)
    recovered = await healthy.run_scheduler_once(
        launcher, owner="scheduler-recovery", lease_seconds=10
    )
    assert recovered[0].accepted is True
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
async def test_accepted_and_terminal_inbox_redeliver_after_restart_until_ack(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    first = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    committed = await first.submit(parent, _delegate("signals"))
    await first.run_scheduler_once(Launcher(), owner="scheduler-a")

    restarted = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    accepted_once = await restarted.pending_signals("parent-run")
    accepted_twice = await restarted.pending_signals("parent-run")
    assert len(accepted_once) == len(accepted_twice) == 1
    assert isinstance(accepted_once[0].signal, ChildAcceptedSignal)
    assert accepted_once[0].record.signal_id == accepted_twice[0].record.signal_id
    await restarted.acknowledge_signal(accepted_once[0].record.signal_id)
    assert await restarted.pending_signals("parent-run") == ()

    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    await uow.finalize(
        committed.child_run_id,
        expected_version=1,
        terminal_status="completed",
        event=RunEventCandidate(
            event_key="child-terminal",
            kind="run.completed",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
            payload={"result": "ok"},
        ),
    )
    await restarted.record_terminal(
        committed.operation_id, status="completed", value={"result": "ok"}
    )
    after_terminal_restart = ChildRunCoordinator(
        SqliteExecutionUnitOfWork(path, clock=clock)
    )
    terminal = await after_terminal_restart.pending_signals("parent-run")
    assert len(terminal) == 1
    assert terminal[0].signal == ChildTerminalSignal(
        "parent-run", "signals", committed.child_run_id, "completed", {"result": "ok"}
    )


@pytest.mark.asyncio
async def test_signal_ack_crash_keeps_inbox_pending_for_recovery(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    healthy = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    await healthy.submit(parent, _delegate("signal-ack-crash"))
    await healthy.run_scheduler_once(Launcher(), owner="scheduler-a")
    delivery = (await healthy.pending_signals("parent-run"))[0]

    def fault(point: str) -> None:
        if point == "child_signal_ack_before_commit":
            raise RuntimeError("crash before signal ack")

    crashing = ChildRunCoordinator(
        SqliteExecutionUnitOfWork(path, clock=clock, fault_injector=fault)
    )
    with pytest.raises(RuntimeError, match="signal ack"):
        await crashing.acknowledge_signal(delivery.record.signal_id)

    redelivered = await healthy.pending_signals("parent-run")
    assert [item.record.signal_id for item in redelivered] == [delivery.record.signal_id]


@pytest.mark.asyncio
async def test_launcher_failure_isolated_between_siblings_and_retry_is_idempotent(tmp_path):
    path = tmp_path / "workflow.db"
    clock = Clock()
    parent = await _parent(path, clock)
    coordinator = ChildRunCoordinator(SqliteExecutionUnitOfWork(path, clock=clock))
    first = await coordinator.submit(parent, _delegate("sibling-a"))
    second = await coordinator.submit(parent, _delegate("sibling-b"))
    launcher = Launcher(fail_once={"sibling-a"})

    attempts = await coordinator.run_scheduler_once(
        launcher, owner="scheduler-a", limit=10, lease_seconds=10
    )

    assert [(item.command.intent.command_id, item.accepted) for item in attempts] == [
        ("sibling-a", False),
        ("sibling-b", True),
    ]
    assert (await coordinator.pending_signals("parent-run"))[0].signal == (
        ChildAcceptedSignal("parent-run", "sibling-b", second.child_run_id)
    )

    clock.advance(11)
    retry = await coordinator.run_scheduler_once(
        launcher, owner="scheduler-b", limit=10, lease_seconds=10
    )
    assert [(item.command.intent.command_id, item.accepted) for item in retry] == [
        ("sibling-a", True)
    ]
    assert launcher.calls.count((first.operation_id, first.child_run_id)) == 2
    async with aiosqlite.connect(path) as db:
        children = await (
            await db.execute(
                """SELECT parent_run_id,COUNT(*) FROM execution_run_links
                WHERE parent_run_id='parent-run' GROUP BY parent_run_id"""
            )
        ).fetchone()
    assert children == ("parent-run", 2)


def test_owner_audit_uses_run_driver_and_structural_link_without_owner_map():
    from pathlib import Path

    source = Path("backend/deskpet/harness/child_runs.py").read_text(encoding="utf-8")
    assert "owner_map" not in source
    assert "_owners" not in source
    assert "execution_runs.driver_kind" in source
    assert "structural links" in source
