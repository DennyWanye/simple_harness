from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from deskpet.execution.contracts import (
    DecisionOpen,
    AttachmentPolicy,
    OutcomeStatus,
    PersistenceLevel,
    RunCreate,
    RunEventCandidate,
    RunStatus,
    fingerprint_json,
)
from deskpet.execution.ledger import ExecutionLedger
from deskpet.harness.decisions import DecisionStore, DecisionWakeupCache
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.kernel import (
    HostContext,
    RegisteredDriver,
    RunKernel,
    RunRequest,
    kernel_public_operations,
)
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    DecisionSignal,
    DelegateRun,
    ChildAcceptedSignal,
    ChildTerminalSignal,
    DriverTerminalCandidate,
    JoinPolicy,
    PersistedEventCandidate,
    TokenCandidate,
)
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter, RouteProfile
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class StaticClassifier:
    def __init__(self, profile: str) -> None:
        self.profile = profile
        self.calls = 0

    def classify(self, request):
        self.calls += 1
        return ClassifiedRoute(self.profile, "test", 1.0)


class FakeDriver:
    def __init__(self) -> None:
        self.starts = 0
        self.recovers = 0
        self.signals = 0
        self.cancelled: list[str] = []

    async def start(self, request):
        self.starts += 1
        yield TokenCandidate(request.run_id, "ok")

    async def recover(self, run_id):
        self.recovers += 1
        if False:
            yield TokenCandidate(run_id, "")

    async def signal(self, signal):
        self.signals += 1
        if False:
            yield TokenCandidate(signal.run_id, "")

    async def cancel(self, run_id, reason):
        self.cancelled.append(reason)
        yield CancelAcknowledgedCandidate(run_id, reason)

    async def close(self):
        return None


class FailingDriver(FakeDriver):
    async def start(self, request):
        raise RuntimeError("isolated boom")
        yield TokenCandidate(request.run_id, "unreachable")


class AtomicStartDriver(FakeDriver):
    def __init__(self, uow) -> None:
        super().__init__()
        self.uow = uow

    async def start(self, request):
        self.starts += 1
        assert request.run_context is not None
        created = await self.uow.create(
            RunCreate(
                run_id=request.run_id,
                idempotency_key=f"workflow:{request.run_id}",
                context=request.run_context,
                payload_fingerprint=fingerprint_json(dict(request.request_payload)),
                capability_fingerprint=request.run_context.capability_hash,
                driver_kind="workflow",
                profile_key=request.profile_key,
                persistence_level=PersistenceLevel.DURABLE,
            )
        )
        accepted = await self.uow.append_event(
            request.run_id,
            expected_version=created.record.version,
            event=RunEventCandidate(
                event_key="accepted",
                kind="workflow.accepted",
                status=OutcomeStatus.ACCEPTED,
                driver_kind="workflow",
            ),
        )
        yield PersistedEventCandidate(accepted)


class DelegateDriver(FakeDriver):
    def __init__(self, join_policy=JoinPolicy.DETACHED) -> None:
        super().__init__()
        self.join_policy = JoinPolicy(join_policy)

    async def start(self, request):
        self.starts += 1
        attachment = {
            JoinPolicy.JOIN_BEFORE_FINAL: AttachmentPolicy.ATTACHED,
            JoinPolicy.ROOT_TERMINAL_CHILD: AttachmentPolicy.ROOT_TERMINAL_CHILD,
            JoinPolicy.DETACHED: AttachmentPolicy.DETACHED,
        }[self.join_policy]
        yield DelegateRun(
            run_id=request.run_id,
            command_id="delegate-1",
            child_request={"task": "child"},
            route_hint="child.default",
            capability_subset=("read_file",),
            attachment_policy=attachment,
            join_policy=self.join_policy,
        )

    async def signal(self, signal):
        self.signals += 1
        self.last_signal = signal
        if False:
            yield TokenCandidate(signal.run_id, "")


class ChildCoordinator:
    def __init__(self) -> None:
        self.submitted = []
        self.scheduler_runs = 0
        self.acked = []
        self.delivery = None

    async def submit(self, parent, command):
        self.submitted.append((parent.run_id, command.command_id))
        self.delivery = SimpleNamespace(
            record=SimpleNamespace(signal_id="signal-1"),
            signal=ChildAcceptedSignal(parent.run_id, command.command_id, "child-1"),
        )
        return SimpleNamespace()

    async def run_scheduler_once(self, launcher, *, owner):
        self.scheduler_runs += 1
        return ()

    async def pending_signals(self, parent_run_id):
        return (self.delivery,) if self.delivery is not None else ()

    async def acknowledge_signal(self, signal_id):
        self.acked.append(signal_id)
        self.delivery = None
        return SimpleNamespace()


class AcceptChild:
    async def accept(self, command):
        return None


class TerminalRaceDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def start(self, request):
        await self.release.wait()
        yield DriverTerminalCandidate(request.run_id, "completed", "model-final")

    async def signal(self, signal):
        yield DriverTerminalCandidate(
            signal.run_id,
            "completed",
            "child-final",
            correlation={"child_run_id": signal.child_run_id},
        )


@pytest_asyncio.fixture
async def kernel(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    classifier = StaticClassifier("react.default")
    driver = FakeDriver()
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(classifier, [RouteProfile("react.default", "react")]),
        drivers=[RegisteredDriver("react", driver)],
    )
    return value, classifier, driver


def host(session: str = "s1") -> HostContext:
    return HostContext(
        session_id=session,
        principal_id=f"principal-{session}",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("primary",),
        trace_id="trace-1",
    )


@pytest.mark.asyncio
async def test_start_routes_once_and_idempotent_retry_does_not_start_twice(kernel) -> None:
    value, classifier, driver = kernel
    request = RunRequest("hello", "req-1", "turn-1")
    first = await value.start(request, host())
    second = await value.start(request, host())
    await asyncio.sleep(0)
    assert first.ref == second.ref
    assert classifier.calls == 1
    assert driver.starts == 1


@pytest.mark.asyncio
async def test_concurrent_same_intent_routes_and_starts_once(kernel) -> None:
    value, classifier, driver = kernel
    request = RunRequest("hello", "req-concurrent", "turn-1")
    first, second = await asyncio.gather(
        value.start(request, host()),
        value.start(request, host()),
    )
    await asyncio.sleep(0)
    assert first.ref == second.ref
    assert classifier.calls == 1
    assert driver.starts == 1


@pytest.mark.asyncio
async def test_observe_preserves_first_live_event_and_foreign_actor_is_rejected(kernel) -> None:
    value, _, _ = kernel
    handle = await value.start(RunRequest("hello", "req-2", "turn-1"), host())
    await asyncio.sleep(0)
    stream = value.observe(handle.ref, host().actor(root_run_id=handle.root_run_id))
    event = await anext(stream)
    await stream.aclose()
    assert event.candidate.payload["text"] == "ok"
    with pytest.raises(Exception):
        await anext(value.observe(handle.ref, host("other").actor()))


@pytest.mark.asyncio
async def test_signal_cancel_recover_and_close_use_explicit_run_scope(kernel) -> None:
    value, _, driver = kernel
    handle = await value.start(RunRequest("hello", "req-3", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    receipt = await value.signal(
        handle.ref,
        actor,
        DecisionSignal(handle.ref.run_id, "d1", {"allow": True}),
    )
    assert receipt.accepted is True
    await value.recover(handle.ref, actor)
    cancelled = await value.cancel(handle.ref, actor, "user_stop")
    assert cancelled.acknowledged is True
    assert cancelled.status is RunStatus.CANCELLED
    assert driver.cancelled == ["user_stop"]
    await value.close(handle.ref, actor)


@pytest.mark.asyncio
async def test_driver_failure_isolated_as_run_terminal(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", FailingDriver())],
    )
    handle = await value.start(RunRequest("hello", "req-fail", "turn-1"), host())
    await asyncio.sleep(0.01)
    record = await ledger.query(handle.ref, host().actor(root_run_id=handle.root_run_id))
    assert record.status is RunStatus.FAILED


@pytest.mark.asyncio
async def test_decision_signal_is_durably_fenced_before_driver_resume(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    driver = FakeDriver()
    decisions = DecisionStore(uow, DecisionWakeupCache())
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
        decision_store=decisions,
    )
    handle = await value.start(RunRequest("hello", "req-decision", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    record = await ledger.query(handle.ref, actor)
    await decisions.open(
        DecisionOpen(
            decision_id="decision-1",
            run_id=handle.ref.run_id,
            nonce="nonce-1",
            kind="clarification",
            prompt_schema_version=1,
            prompt={"question": "continue?"},
            expires_at=None,
        ),
        actor,
        expected_run_version=record.version,
    )

    receipt = await value.signal(
        handle.ref,
        actor,
        DecisionSignal(
            handle.ref.run_id,
            "decision-1",
            {"answer": "yes"},
            "nonce-1",
            0,
        ),
    )
    assert receipt.accepted is True
    assert driver.signals == 1

    with pytest.raises(Exception):
        await value.signal(
            handle.ref,
            actor,
            DecisionSignal(
                handle.ref.run_id,
                "decision-1",
                {"answer": "again"},
                "wrong-nonce",
                0,
            ),
        )


@pytest.mark.asyncio
async def test_atomic_driver_commits_before_kernel_returns_handle(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    driver = AtomicStartDriver(uow)
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(
            StaticClassifier("durable.default"),
            [RouteProfile("durable.default", "workflow")],
        ),
        drivers=[
            RegisteredDriver(
                "workflow",
                driver,
                durable_from_start=True,
                atomic_start=True,
            )
        ],
    )
    handle = await value.start(
        RunRequest("long task", "req-atomic", "turn-1"),
        host(),
    )
    actor = host().actor(root_run_id=handle.root_run_id)
    record = await ledger.query(handle.ref, actor)
    assert record.spec.profile_key == "durable.default"
    stream = value.observe(handle.ref, actor)
    accepted = await anext(stream)
    await stream.aclose()
    assert accepted.kind == "workflow.accepted"
    assert accepted.durable_seq == 1
    assert driver.starts == 1


@pytest.mark.asyncio
async def test_delegate_is_durable_before_child_signal_reaches_driver(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    driver = DelegateDriver()
    children = ChildCoordinator()
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
        child_runs=children,
        child_launcher=SimpleNamespace(),
    )
    handle = await value.start(RunRequest("delegate", "req-child", "turn-1"), host())
    await asyncio.sleep(0.01)
    assert children.submitted == [(handle.ref.run_id, "delegate-1")]
    assert children.scheduler_runs == 1
    assert children.acked == ["signal-1"]
    assert driver.last_signal == ChildAcceptedSignal(
        handle.ref.run_id, "delegate-1", "child-1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("join_policy", "child_cancelled"),
    [
        (JoinPolicy.JOIN_BEFORE_FINAL, True),
        (JoinPolicy.ROOT_TERMINAL_CHILD, True),
        (JoinPolicy.DETACHED, False),
    ],
)
async def test_cancel_cascades_only_by_attachment_policy(
    tmp_path, join_policy, child_cancelled
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    driver = DelegateDriver(join_policy)
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
        child_runs=ChildRunCoordinator(uow),
        child_launcher=AcceptChild(),
    )
    handle = await value.start(RunRequest("delegate", "req-tree", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    children = ()
    for _ in range(100):
        children = await ledger.list_children(handle.ref, actor)
        if children:
            break
        await asyncio.sleep(0.01)
    assert len(children) == 1

    await value.cancel(handle.ref, actor, "user_stop")

    child = await ledger.query(
        type(handle.ref)(children[0].run_id, handle.ref.expected_session_id),
        actor,
    )
    assert (child.status is RunStatus.CANCELLED) is child_cancelled


@pytest.mark.asyncio
async def test_root_and_child_terminal_race_has_one_winner(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    driver = TerminalRaceDriver()
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
    )
    handle = await value.start(RunRequest("race", "req-race", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    child_signal = ChildTerminalSignal(
        handle.ref.run_id,
        "delegate-1",
        "child-1",
        "completed",
        "child result",
    )
    signal_task = asyncio.create_task(value.signal(handle.ref, actor, child_signal))
    driver.release.set()
    await signal_task
    await asyncio.sleep(0.02)

    record = await ledger.query(handle.ref, actor)
    events = await uow.list_events(handle.ref.run_id)
    terminal = [event for event in events if event.kind == "final"]
    assert record.status is RunStatus.COMPLETED
    assert len(terminal) == 1


def test_kernel_surface_is_six_operations_and_has_no_product_branches() -> None:
    assert kernel_public_operations() == ("start", "observe", "signal", "cancel", "recover", "close")
    source = Path(__file__).parents[2] / "deskpet" / "harness" / "kernel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    kernel_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RunKernel"
    )
    public = {
        node.name
        for node in kernel_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public == set(kernel_public_operations())
    text = source.read_text(encoding="utf-8").casefold()
    for product in ("deepresearch", "deep_research", "ppt", "code_complex", "voice"):
        assert product not in text
