from __future__ import annotations

import asyncio
import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import aiosqlite

from deskpet.execution.contracts import (
    ActorContext,
    AuthorizationError,
    DecisionOpen,
    AttachmentPolicy,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    RunStatus,
    fingerprint_json,
)
from deskpet.harness.context import HostContextFactory
from deskpet.harness.bootstrap import HarnessRuntime
from deskpet.harness.drivers.react import (
    ReActDriver,
    ReactFinal,
    ReactToolBatch,
)
from deskpet.harness.child_runs import (
    ChildRunCoordinator,
    ChildRunScheduler as _ChildRunScheduler,
)
from deskpet.harness.kernel import (
    HostContext,
    KernelChildLauncher,
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
    DriverEvent,
    DriverSignal,
    DriverTerminalCandidate,
    ExecuteTools,
    JoinPolicy,
    OpenDecision,
    PersistedEventCandidate,
    TokenCandidate,
)
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter, RouteProfile
from deskpet.harness.tool_executor import UnifiedToolExecutor
from deskpet.harness.runtime import DriverRuntime
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.recovery import HarnessRecoveryCoordinator
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall


class ChildRunScheduler(_ChildRunScheduler):
    async def reconcile_once(self, **kwargs) -> None:
        await self.reconcile_commands_once(**kwargs)
        command_errors = self.last_errors
        await self.reconcile_signals_once(
            recovery_lease=kwargs.get("recovery_lease")
        )
        self.last_errors = command_errors + self.last_errors


def stable_child_operation_id(parent_run_id: str, command_id: str) -> str:
    return hashlib.sha256(
        f"execution-child-operation|{parent_run_id}|{command_id}".encode()
    ).hexdigest()


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

    async def recover(self, run_id, recovery_lease):
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


class RecoveryOrderDriver(FakeDriver):
    def __init__(self, timeline) -> None:
        super().__init__()
        self.timeline = timeline

    def recover(self, run_id, recovery_lease):
        self.timeline.append("recover")

        async def candidates():
            self.timeline.append("anext")
            if False:
                yield TokenCandidate(run_id, "")

        return candidates()


class ImmediateTerminalDriver(FakeDriver):
    async def start(self, request):
        self.starts += 1
        yield TokenCandidate(request.run_id, "ok")
        yield DriverTerminalCandidate(request.run_id, "completed", "ok")


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
        self.acked = []
        self.delivery = None

    async def submit(self, parent, command):
        self.submitted.append((parent.run_id, command.command_id))
        self.delivery = SimpleNamespace(
            record=SimpleNamespace(signal_id="signal-1"),
            signal=ChildAcceptedSignal(parent.run_id, command.command_id, "child-1"),
        )
        return SimpleNamespace()

    async def pending_signals(self, parent_run_id):
        return (self.delivery,) if self.delivery is not None else ()

    async def acknowledge_signal(self, signal_id):
        self.acked.append(signal_id)
        self.delivery = None
        return SimpleNamespace()


class AcceptChild:
    async def accept(self, command):
        return None

    async def deliver(self, parent, delivery, recovery_lease):
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


class ToolLoopDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.outcomes = ()

    async def start(self, request):
        call = PreparedToolCall.prepare(
            tool_name="read_probe", stable_call_id="call-1",
            final_params={"value": 7}, tool_spec_version="1",
            schema_hash="schema", permission_policy_version="1",
            effect_type="idempotent_read",
        )
        context = ToolExecutionContext(
            scope_id="scope-1",
            session_id=request.session_id,
            request_id="request-1",
            root_run_id=request.run_id,
            run_id=request.run_id,
            call_id=call.stable_call_id,
            effect_id="effect-1",
            capability_hash="c" * 64,
            scope_hash="scope-1",
        )
        yield ExecuteTools(request.run_id, "tools-1", (call,), (context,), (0,))

    async def signal(self, signal):
        self.signals += 1
        self.outcomes = signal.outcomes
        yield DriverTerminalCandidate(signal.run_id, "completed", "tool complete")


class MixedLateCollaborator:
    def __init__(self) -> None:
        self.resumes = []

    async def start(self, request):
        calls = tuple(
            PreparedToolCall.prepare(
                tool_name=name, stable_call_id=f"call-{index}",
                final_params={"value": index}, tool_spec_version="1",
                schema_hash="schema", permission_policy_version="1",
                effect_type="opaque_manual", effect_policy_version="v1",
            )
            for index, name in enumerate(("ready-write", "late-write"), 1)
        )
        contexts = tuple(
            ToolExecutionContext(
                scope_id="scope-1", session_id=request.session_id,
                request_id="request-1", root_run_id=request.run_id,
                run_id=request.run_id, call_id=call.stable_call_id,
                effect_id=f"effect-{index}", capability_hash="c" * 64,
                scope_hash="scope-1",
            )
            for index, call in enumerate(calls, 1)
        )
        yield ReactToolBatch("tools-mixed", calls, contexts)

    async def resume(self, boundary, response):
        self.resumes.append((boundary, response))
        if False:
            yield ReactFinal("unreachable")

    async def cancel(self, run_id, reason):
        return None

    async def close(self):
        return None


class MixedLateRegistry:
    def __init__(self) -> None:
        self.outcomes = {}
        self.metadata = {}
        self.contexts = {}

    def prepared_execution_policy(self, call):
        return False, True

    def is_concurrency_safe(self, tool_name):
        return True

    def get(self, tool_name):
        return SimpleNamespace(completion_semantics="immediate")

    def prepared_outcome_status(self, call, outcome):
        return OutcomeStatus.UNKNOWN if outcome.state.value == "malformed" else OutcomeStatus.SUCCEEDED

    async def execute_prepared(self, call, *, effect_id, **kwargs):
        self.contexts[effect_id] = kwargs.get("execution_context")
        if call.tool_name == "late-write":
            self.metadata[effect_id] = {"late_pending": True}
            return NormalizedToolOutcome.malformed("physical call still running")
        outcome = NormalizedToolOutcome.success({"written": call.final_params["value"]})
        self.outcomes[effect_id] = outcome
        self.metadata[effect_id] = {"receipt_ref": "receipt-ready"}
        return outcome

    def take_prepared_execution_metadata(self, effect_id):
        return dict(self.metadata.get(effect_id, {}))

    def acknowledge_prepared_effect(self, effect_id):
        self.outcomes.pop(effect_id, None)
        self.metadata.pop(effect_id, None)

    async def observe_late_prepared(self, effect_id):
        outcome = self.outcomes.get(effect_id)
        return ("complete", outcome) if outcome is not None else ("pending", None)

    def ready_late_prepared_run_ids(self):
        return frozenset(
            self.contexts[effect_id].run_id for effect_id in self.outcomes
            if effect_id == "effect-2"
        )

    async def close_prepared_executions(self, timeout):
        deadline = asyncio.get_running_loop().time() + timeout
        while "effect-2" not in self.outcomes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.001)

    def complete_late(self) -> None:
        self.outcomes["effect-2"] = NormalizedToolOutcome.success({"written": 2})
        self.metadata["effect-2"] = {
            "receipt_ref": "receipt-late", "evidence_verified": True,
            "outcome_status": "succeeded",
        }


class PreparedRegistry:
    def is_concurrency_safe(self, tool_name):
        return True

    def prepared_execution_policy(self, call):
        return False, False

    def get(self, tool_name):
        return SimpleNamespace(completion_semantics="immediate")

    def prepared_outcome_status(self, call, outcome):
        return OutcomeStatus.SUCCEEDED

    def take_prepared_execution_metadata(self, effect_id):
        return {}

    def acknowledge_prepared_effect(self, effect_id):
        return None

    async def observe_late_prepared(self, effect_id):
        return "missing", None

    def ready_late_prepared_run_ids(self):
        return frozenset()

    async def execute_prepared(self, call, **kwargs):
        return NormalizedToolOutcome.success({"seen": call.final_params["value"]})


class ReuseOnlyRegistry:
    def is_concurrency_safe(self, tool_name):
        return True

    def prepared_execution_policy(self, call):
        raise AssertionError("authoritative reuse must not inspect live tool policy")

    def take_prepared_execution_metadata(self, effect_id):
        return {}

    def acknowledge_prepared_effect(self, effect_id):
        return None


class InterleavingRegistry:
    def __init__(self) -> None:
        self.physical_calls = []
        self.acknowledged = []

    def prepared_execution_policy(self, call):
        return False, True

    def is_concurrency_safe(self, tool_name):
        return True

    async def execute_prepared(self, call, **kwargs):
        self.physical_calls.append(call.stable_call_id)
        return NormalizedToolOutcome.success({"call_id": call.stable_call_id})

    def prepared_outcome_status(self, call, outcome):
        if call.stable_call_id == "call-in-flight":
            raise AssertionError("in-flight placeholder must not inspect live tool policy")
        return OutcomeStatus.SUCCEEDED

    def take_prepared_execution_metadata(self, effect_id):
        return {"receipt_ref": f"receipt:{effect_id}"}

    async def observe_late_prepared(self, effect_id):
        return "pending", None

    def acknowledge_prepared_effect(self, effect_id):
        self.acknowledged.append(effect_id)


class RecordingSignalDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.last_signal = None

    async def signal(self, signal):
        self.last_signal = signal
        if False:
            yield TokenCandidate(signal.run_id, "")


class NoEffects:
    def prepared_execution_policy(self, call):
        return False, False


class ClarificationCollaborator:
    async def start(self, request):
        yield OpenDecision(
                run_id=request.run_id,
                command_id="clarify-command",
                decision_id="clarify-1",
                nonce="clarify-nonce",
                kind="clarification",
                prompt={"question": "continue?"},
            )

    async def resume(self, boundary, response):
        yield ReactFinal("continued")

    async def cancel(self, run_id, reason):
        return None

    async def close(self):
        return None


@pytest_asyncio.fixture
async def kernel(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    classifier = StaticClassifier("react.default")
    driver = FakeDriver()
    value = RunKernel(
        uow=uow,
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
async def test_short_react_run_stays_in_bounded_kernel_index_without_sqlite_write(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", ImmediateTerminalDriver())],
        max_live_runs=4,
    )
    handle = await value.start(RunRequest("hello", "req-live", "turn-live"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    assert [event async for event in value.observe(handle.ref, actor)][-1].kind == "final"
    async with aiosqlite.connect(path) as db:
        rows = (await (await db.execute("SELECT COUNT(*) FROM execution_runs")).fetchone())[0]
    assert rows == 0
    await value.close(handle.ref, actor)
    assert handle.ref.run_id not in value._active


@pytest.mark.asyncio
async def test_first_decision_atomically_promotes_boundary_and_kernel_adopts_uow(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.activate_empty_runtime()
    driver = ReActDriver(ClarificationCollaborator(), uow, NoEffects())
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver)],
    )
    handle = await value.start(RunRequest("clarify", "req-boundary", "turn-boundary"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    stream = value.observe(handle.ref, actor)
    decision_event = await anext(stream)
    assert decision_event.kind == "decision"
    durable = await uow.query(handle.ref, actor)
    continuation = await uow.load_continuation(handle.ref.run_id)
    assert durable.persistence_level is PersistenceLevel.DURABLE
    assert continuation is not None and continuation.pending_decision_id == "clarify-1"
    assert value._active[handle.ref.run_id].record is None

    receipt = await value.signal(
        handle.ref,
        actor,
        DecisionSignal(
            handle.ref.run_id,
            "clarify-1",
            {"answer": "yes"},
            nonce="clarify-nonce",
            version=0,
        ),
    )
    assert receipt.accepted is True
    assert [event async for event in stream][-1].kind == "final"


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
async def test_live_run_authorization_rejects_cross_scope_actors(kernel) -> None:
    value, _, _ = kernel
    handle = await value.start(RunRequest("hello", "req-auth", "turn-1"), host())
    invalid = (
        ActorContext("principal-s1", "other-session", 1, handle.root_run_id),
        ActorContext("principal-s1", "s1", 1, "other-root"),
        ActorContext("other-principal", "s1", 1, handle.root_run_id),
        ActorContext("principal-s1", "s1", 2, handle.root_run_id),
    )

    for actor in invalid:
        with pytest.raises(AuthorizationError) as rejected:
            await anext(value.observe(handle.ref, actor))
        assert rejected.value.code == "actor_not_authorized"


@pytest.mark.asyncio
async def test_signal_cancel_recover_and_close_use_explicit_run_scope(kernel) -> None:
    value, _, driver = kernel
    handle = await value.start(RunRequest("hello", "req-3", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    receipt = await value.signal(
        handle.ref,
        actor,
        ChildAcceptedSignal(handle.ref.run_id, "command-1", "child-1"),
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
    await uow.initialize()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", FailingDriver())],
    )
    handle = await value.start(RunRequest("hello", "req-fail", "turn-1"), host())
    stream = value.observe(
        handle.ref, host().actor(root_run_id=handle.root_run_id)
    )
    terminal = [event async for event in stream][-1]
    assert terminal.status is OutcomeStatus.FAILED
    active = value._active[handle.ref.run_id]
    assert active.task is None
    assert active.subscribers == set()
    await value.close(
        handle.ref, host().actor(root_run_id=handle.root_run_id)
    )
    assert handle.ref.run_id not in value._active


@pytest.mark.asyncio
async def test_recovery_renews_before_driver_and_first_anext(tmp_path) -> None:
    timeline: list[str] = []

    class TrackingUow(SqliteExecutionUnitOfWork):
        async def renew_recovery(self, lease, *, lease_seconds=30.0):
            timeline.append("renew")
            return await super().renew_recovery(lease, lease_seconds=lease_seconds)

    uow = TrackingUow(tmp_path / "workflow.db")
    context = HostContextFactory().create_run_context(
        session_id="s1",
        root_run_id="run-recovery-order",
        request_id="request-recovery-order",
        turn_id="turn-recovery-order",
        venue="text",
        capability_hash="c" * 64,
        provider_plan=("primary",),
        trace_id="trace-order",
        principal_id="principal-s1",
        auth_epoch=1,
    )
    await uow.create(
        RunCreate(
            run_id="run-recovery-order",
            idempotency_key="root:s1:request-recovery-order",
            context=context,
            payload_fingerprint=fingerprint_json({"text": "recover"}),
            capability_fingerprint="c" * 64,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
        )
    )
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", RecoveryOrderDriver(timeline))],
    )
    actor = host().actor(root_run_id="run-recovery-order")
    await value.recover(RunRef("run-recovery-order", "s1"), actor)
    await value._active["run-recovery-order"].task

    assert timeline == ["renew", "recover", "renew", "anext"]


@pytest.mark.asyncio
async def test_decision_signal_is_durably_fenced_before_driver_resume(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = FakeDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
    )
    handle = await value.start(RunRequest("hello", "req-decision", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    record = await uow.query(handle.ref, actor)
    await uow.open_decision(
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
    await uow.initialize()
    driver = AtomicStartDriver(uow)
    value = RunKernel(
        uow=uow,
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
    record = await uow.query(handle.ref, actor)
    assert record.spec.profile_key == "durable.default"
    stream = value.observe(handle.ref, actor)
    accepted = await anext(stream)
    await stream.aclose()
    assert accepted.kind == "workflow.accepted"
    assert accepted.durable_seq == 1
    assert driver.starts == 1

    restarted = RunKernel(
        uow=SqliteExecutionUnitOfWork(tmp_path / "workflow.db"),
        router=RegisteredRouter(
            StaticClassifier("durable.default"),
            [RouteProfile("durable.default", "workflow")],
        ),
        drivers=[
            RegisteredDriver(
                "workflow",
                AtomicStartDriver(uow),
                durable_from_start=True,
                atomic_start=True,
            )
        ],
    )
    assert handle.ref.run_id not in restarted._active
    hydrated = restarted.observe(handle.ref, actor)
    replayed = await anext(hydrated)
    await hydrated.aclose()
    assert replayed.event_id == accepted.event_id
    assert replayed.durable_seq == 1


@pytest.mark.asyncio
async def test_delegate_only_commits_command_without_inline_scheduler(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = DelegateDriver()
    children = ChildCoordinator()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
        child_runs=children,
    )
    handle = await value.start(RunRequest("delegate", "req-child", "turn-1"), host())
    await asyncio.sleep(0.01)
    assert children.submitted == [(handle.ref.run_id, "delegate-1")]
    assert children.acked == []
    assert driver.signals == 0


@pytest.mark.asyncio
async def test_scheduled_child_replay_uses_authoritative_terminal_row(tmp_path) -> None:
    path = tmp_path / "workflow.db"

    def fault(point: str) -> None:
        if point == "child_ack_before_commit":
            raise RuntimeError("crash before child command ack")

    uow = SqliteExecutionUnitOfWork(path, fault_injector=fault)
    await uow.initialize()
    context = RunContext(
        session_id="s1", root_run_id="parent-authoritative", parent_run_id=None,
        request_id="req-parent", turn_id="turn-parent", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="trace-parent", principal_id="p1", auth_epoch=0,
    )
    parent = (await uow.create(RunCreate(
        run_id="parent-authoritative", idempotency_key="root:parent-authoritative",
        context=context, payload_fingerprint="a" * 64,
        capability_fingerprint="c" * 64, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    ))).record

    class ChildTerminalDriver(FakeDriver):
        async def start(self, request):
            self.starts += 1
            yield DriverTerminalCandidate(request.run_id, "completed", "done")

        async def signal(self, signal, recovery_lease=None):
            if False:
                yield TokenCandidate(signal.run_id, "")

    driver = ChildTerminalDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
    )
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, DelegateRun(
        run_id=parent.run_id, command_id="terminal-replay",
        child_request={"task": "finish", "driver_kind": "react"},
        route_hint="react.default", capability_subset=(),
        attachment_policy=AttachmentPolicy.DETACHED,
        join_policy=JoinPolicy.DETACHED,
    ))
    failed = ChildRunScheduler(
        coordinator, KernelChildLauncher(kernel), owner="crashing-scheduler"
    )
    await failed.reconcile_once(lease_seconds=0.001)
    assert any("command ack" in error for error in failed.last_errors)
    child_ref = RunRef(command.child_run_id, context.session_id)
    actor = ActorContext("p1", "s1", 0, root_run_id=context.root_run_id)
    for _ in range(100):
        child = await uow.query(child_ref, actor)
        if child.status is RunStatus.COMPLETED:
            break
        await asyncio.sleep(0.01)
    assert child.status is RunStatus.COMPLETED
    assert (await uow.get_child_command(command.operation_id)).status.value == "scheduled"
    assert driver.starts == 1
    for _ in range(100):
        if command.child_run_id not in kernel._active:
            break
        await asyncio.sleep(0.01)
    assert command.child_run_id not in kernel._active

    await asyncio.sleep(0.01)
    async with kernel._lock:
        stale = kernel._live.add(command.child_run_id, actor)
        stale.record = child
    assert command.child_run_id in kernel._active
    fresh = SqliteExecutionUnitOfWork(path)
    await ChildRunScheduler(
        ChildRunCoordinator(fresh), KernelChildLauncher(kernel), owner="restarted-scheduler"
    ).reconcile_once(lease_seconds=1)

    assert (await fresh.get_child_command(command.operation_id)).status.value == "acked"
    assert driver.starts == 1
    assert command.child_run_id not in kernel._active


@pytest.mark.asyncio
async def test_precreated_child_with_continuation_recovers_instead_of_restarting(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    context = RunContext(
        session_id="s1", root_run_id="parent-recover-child", parent_run_id=None,
        request_id="req-parent", turn_id="turn-parent", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="trace-parent", principal_id="p1", auth_epoch=0,
    )
    parent = (await uow.create(RunCreate(
        run_id="parent-recover-child", idempotency_key="root:parent-recover-child",
        context=context, payload_fingerprint="a" * 64,
        capability_fingerprint="c" * 64, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    ))).record
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, DelegateRun(
        run_id=parent.run_id, command_id="resume-existing",
        child_request={"task": "resume", "driver_kind": "react"},
        route_hint="react.default", capability_subset=(),
        attachment_policy=AttachmentPolicy.DETACHED,
        join_policy=JoinPolicy.DETACHED,
    ))
    leased = (await uow.lease_child_commands(
        owner="setup", limit=1, lease_seconds=30
    ))[0]
    scheduled = await uow.schedule_child_command(
        leased.operation_id, lease_owner="setup",
        lease_epoch=leased.schedule_lease_epoch,
    )
    await uow.save_continuation(
        command.child_run_id, 0, {"checkpoint": "already-started"}
    )

    driver = FakeDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
    )
    await KernelChildLauncher(kernel).accept(scheduled)
    await kernel._active[command.child_run_id].task

    assert driver.starts == 0
    assert driver.recovers == 1


@pytest.mark.asyncio
async def test_slow_child_signal_renews_parent_lease_before_fenced_write(tmp_path) -> None:
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    class TrackingUow(SqliteExecutionUnitOfWork):
        renewals = 0

        async def renew_recovery(self, lease, *, lease_seconds=30.0):
            self.renewals += 1
            return await super().renew_recovery(lease, lease_seconds=lease_seconds)

    class SlowSignalDriver(FakeDriver):
        async def signal(self, signal, recovery_lease=None):
            for _ in range(4):
                prior = uow.renewals
                clock.advance(8)
                for _ in range(100):
                    if uow.renewals > prior:
                        break
                    await asyncio.sleep(0.005)
                assert uow.renewals > prior
            await uow.save_continuation(
                signal.run_id, 0, {"slow_signal_applied": True},
                recovery_lease=recovery_lease,
            )
            if False:
                yield TokenCandidate(signal.run_id, "")

    clock = Clock()
    uow = TrackingUow(tmp_path / "workflow.db", clock=clock)
    await uow.initialize()
    context = RunContext(
        session_id="s1", root_run_id="slow-parent", parent_run_id=None,
        request_id="slow-request", turn_id="slow-turn", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="slow-trace", principal_id="p1", auth_epoch=0,
    )
    parent = (await uow.create(RunCreate(
        run_id="slow-parent", idempotency_key="root:slow-parent", context=context,
        payload_fingerprint="a" * 64, capability_fingerprint="c" * 64,
        driver_kind="react", profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE, status=RunStatus.RUNNING,
    ))).record
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, DelegateRun(
        run_id=parent.run_id, command_id="slow-signal",
        child_request={"task": "slow", "driver_kind": "react"},
        route_hint="react.default", capability_subset=(),
        attachment_policy=AttachmentPolicy.DETACHED,
        join_policy=JoinPolicy.DETACHED,
    ))
    leased = (await uow.lease_child_commands(
        owner="setup", limit=1, lease_seconds=30
    ))[0]
    scheduled = await uow.schedule_child_command(
        command.operation_id, lease_owner="setup",
        lease_epoch=leased.schedule_lease_epoch,
    )
    await uow.acknowledge_child_command(
        command.operation_id, lease_owner="setup",
        lease_epoch=scheduled.schedule_lease_epoch,
    )
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", SlowSignalDriver(), durable_from_start=True)],
        child_signal_heartbeat_interval=0.005,
    )

    scheduler = ChildRunScheduler(
        coordinator, KernelChildLauncher(kernel), owner="slow-scheduler"
    )
    await scheduler.reconcile_signals_once()

    assert scheduler.last_errors == ()
    continuation = await uow.load_continuation(parent.run_id)
    assert continuation is not None
    assert dict(continuation.payload) == {"slow_signal_applied": True}
    assert uow.renewals >= 4
    assert clock.now > 1030


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
    await uow.initialize()
    driver = DelegateDriver(join_policy)
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
        child_runs=ChildRunCoordinator(uow),
    )
    handle = await value.start(RunRequest("delegate", "req-tree", "turn-1"), host())
    operation_id = stable_child_operation_id(handle.ref.run_id, "delegate-1")
    for _ in range(100):
        if await uow.get_child_command(operation_id) is not None:
            break
        await asyncio.sleep(0.01)
    await ChildRunScheduler(
        ChildRunCoordinator(uow), AcceptChild(), owner="test-child"
    ).reconcile_once()
    actor = host().actor(root_run_id=handle.root_run_id)
    child_links = ()
    for _ in range(100):
        child_links = await uow.list_child_links(handle.ref, actor)
        if child_links:
            break
        await asyncio.sleep(0.01)
    assert len(child_links) == 1

    await value.cancel(handle.ref, actor, "user_stop")

    child = await uow.query(
        type(handle.ref)(child_links[0].child_run_id, handle.ref.expected_session_id),
        actor,
    )
    assert (child.status is RunStatus.CANCELLED) is child_cancelled


@pytest.mark.asyncio
async def test_root_and_child_terminal_race_has_one_winner(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = TerminalRaceDriver()
    value = RunKernel(
        uow=uow,
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

    record = await uow.query(handle.ref, actor)
    events = await uow.list_events(handle.ref.run_id)
    terminal = [event for event in events if event.kind == "final"]
    assert record.status is RunStatus.COMPLETED
    assert len(terminal) == 1
    await asyncio.sleep(0.05)
    await value.close(handle.ref, actor)


@pytest.mark.asyncio
async def test_kernel_executes_tool_command_and_resumes_same_driver(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = ToolLoopDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
        tool_executor=UnifiedToolExecutor(PreparedRegistry()),
    )

    handle = await value.start(RunRequest("tool", "req-tool", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    for _ in range(100):
        record = await uow.query(handle.ref, actor)
        if record.status is RunStatus.COMPLETED:
            break
        await asyncio.sleep(0.01)

    assert record.status is RunStatus.COMPLETED
    assert driver.signals == 1
    assert driver.outcomes[0].value == {"seen": 7}


@pytest.mark.asyncio
async def test_runtime_keeps_mixed_batch_pending_when_one_physical_call_is_late(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    registry, collaborator = MixedLateRegistry(), MixedLateCollaborator()
    driver = ReActDriver(collaborator, uow, registry)
    executor = UnifiedToolExecutor(registry)
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver)],
        tool_executor=executor,
    )
    recovery = HarnessRecoveryCoordinator(uow, value, executor)
    await recovery.start(interval=0.005)
    handle = await value.start(RunRequest("mixed", "req-mixed", "turn-1"), host())
    for _ in range(100):
        async with aiosqlite.connect(tmp_path / "workflow.db") as db:
            settled = await (
                await db.execute(
                    "SELECT status FROM execution_effects WHERE effect_id='effect-1'"
                )
            ).fetchone()
        if settled is not None and settled[0] == "succeeded":
            break
        await asyncio.sleep(0.01)

    assert collaborator.resumes == []
    async with aiosqlite.connect(tmp_path / "workflow.db") as db:
        rows = await (
            await db.execute(
                "SELECT effect_id,status,receipt_ref FROM execution_effects ORDER BY effect_id"
            )
        ).fetchall()
        late_attempt = await (
            await db.execute(
                "SELECT status FROM execution_effect_attempts WHERE effect_id='effect-2'"
            )
        ).fetchone()
    assert rows == [
        ("effect-1", "succeeded", "receipt-ready"),
        ("effect-2", "unknown", None),
    ]
    assert late_attempt == ("unknown",)
    continuation = await uow.load_continuation(handle.ref.run_id)
    assert continuation is not None
    payload = dict(continuation.payload)
    assert payload["outcomes"][0]["state"] == "success"
    assert payload["outcomes"][1] is None
    record = await uow.query(handle.ref, host().actor(root_run_id=handle.root_run_id))
    assert record.status is not RunStatus.COMPLETED

    for _ in range(100):
        active_task = value._active[handle.ref.run_id].task
        if active_task is not None and active_task.done():
            break
        await asyncio.sleep(0.01)
    assert (await registry.observe_late_prepared("effect-1"))[0] == "pending"
    asyncio.get_running_loop().call_later(0.01, registry.complete_late)
    await HarnessRuntime(
        value, None, None, None, recovery, None,
        (RegisteredDriver("react", driver),), executor,
    ).close(timeout=0.2)
    async with aiosqlite.connect(tmp_path / "workflow.db") as db:
        late = await (
            await db.execute(
                "SELECT status,receipt_ref FROM execution_effects WHERE effect_id='effect-2'"
            )
        ).fetchone()
    assert late == ("succeeded", "receipt-late")
    for _ in range(100):
        if collaborator.resumes:
            break
        await asyncio.sleep(0.01)
    assert len(collaborator.resumes) == 1
    recovered_boundary = collaborator.resumes[0][0]
    assert [item.value["written"] for item in recovered_boundary.outcomes] == [1, 2]
    for _ in range(100):
        if value._active[handle.ref.run_id].task.done():
            break
        await asyncio.sleep(0.01)
    assert value._active[handle.ref.run_id].task.done()


@pytest.mark.asyncio
async def test_restart_after_close_bound_keeps_missing_late_evidence_unknown(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    registry, first_collaborator = MixedLateRegistry(), MixedLateCollaborator()
    first_driver = ReActDriver(first_collaborator, uow, registry)
    first_executor = UnifiedToolExecutor(registry)
    first = RunKernel(
        uow=uow,
        router=RegisteredRouter(StaticClassifier("react.default"),
                                [RouteProfile("react.default", "react")]),
        drivers=[RegisteredDriver("react", first_driver)],
        tool_executor=first_executor,
    )
    handle = await first.start(RunRequest("mixed", "req-restart", "turn-1"), host())
    for _ in range(100):
        async with aiosqlite.connect(path) as db:
            row = await (await db.execute(
                "SELECT status FROM execution_effects WHERE effect_id='effect-2'"
            )).fetchone()
        if row == ("unknown",):
            break
        await asyncio.sleep(0.01)
    assert row == ("unknown",)
    first_recovery = HarnessRecoveryCoordinator(uow, first, first_executor)
    await HarnessRuntime(
        first, None, None, None, first_recovery, None,
        (RegisteredDriver("react", first_driver),), first_executor,
    ).close(timeout=0.01)

    restarted_uow = SqliteExecutionUnitOfWork(path)
    await restarted_uow.initialize()
    restarted_collaborator = MixedLateCollaborator()
    missing = PreparedRegistry()
    restarted = RunKernel(
        uow=restarted_uow,
        router=RegisteredRouter(StaticClassifier("react.default"),
                                [RouteProfile("react.default", "react")]),
        drivers=[RegisteredDriver(
            "react", ReActDriver(restarted_collaborator, restarted_uow, missing)
        )],
        tool_executor=UnifiedToolExecutor(missing),
    )
    await HarnessRecoveryCoordinator(restarted_uow, restarted).recover_pending()
    for _ in range(300):
        if restarted_collaborator.resumes:
            break
        await asyncio.sleep(0.01)
    assert restarted_collaborator.resumes[0][0].outcome_statuses[1] is OutcomeStatus.UNKNOWN
    async with aiosqlite.connect(path) as db:
        stored = await (await db.execute(
            "SELECT status,outcome_json FROM execution_effects WHERE effect_id='effect-2'"
        )).fetchone()
    assert stored[0] == "unknown"
    assert "reconciliation_pending" not in stored[1]
    for _ in range(300):
        if restarted._active[handle.ref.run_id].task.done():
            break
        await asyncio.sleep(0.01)
    assert restarted._active[handle.ref.run_id].task.done()
    await asyncio.sleep(0.05)  # let aiosqlite worker threads publish their final close


@pytest.mark.asyncio
async def test_runtime_reuses_settled_effect_without_live_registry_policy(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    run_id = "reuse-run"
    context = RunContext(
        session_id="s1", root_run_id=run_id, parent_run_id=None,
        request_id="request-reuse", turn_id="turn-reuse", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="trace-reuse", principal_id="principal-s1", auth_epoch=1,
    )
    spec = RunCreate(
        run_id=run_id, idempotency_key="root:s1:request-reuse:turn-reuse", context=context,
        payload_fingerprint=fingerprint_json({"reuse": True}),
        capability_fingerprint=context.capability_hash, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.DURABLE,
    )
    record = (await uow.create(spec)).record
    call = PreparedToolCall.prepare(
        tool_name="removed-write", stable_call_id="call-reuse",
        final_params={"value": 1}, tool_spec_version="old",
        schema_hash="old-schema", permission_policy_version="old-permission",
        effect_type="opaque_manual", effect_policy_version="old-effect",
    )
    tool_context = ToolExecutionContext(
        scope_id="scope", session_id="s1", request_id=context.request_id,
        root_run_id=run_id, run_id=run_id, call_id=call.stable_call_id,
        effect_id="effect-reuse", capability_hash=context.capability_hash,
        scope_hash="scope",
    )
    claim = await uow.consume_grant_and_claim_effect(
        None, host().actor(root_run_id=run_id), run_id=run_id,
        expected_session_id="s1", call_id=call.stable_call_id,
        effect_id=tool_context.effect_id, tool_name=call.tool_name,
        args_hash=call.args_hash, capability_hash=tool_context.capability_hash,
        scope_hash=tool_context.scope_hash, effect_type=call.effect_type,
        policy={"kind": call.effect_type, "version": call.effect_policy_version},
        prepared=call.to_dict(), worker_owner="kernel", worker_epoch=1,
    )
    await uow.save_continuation(run_id, 0, {"pending": True})
    outcome = NormalizedToolOutcome.success({"reused": True})
    await uow.settle_effect_and_advance_boundary(
        tool_context.effect_id, expected_effect_version=claim.effect_version,
        attempt_no=claim.attempt_no, worker_owner=claim.worker_owner,
        worker_epoch=claim.worker_epoch, status="succeeded", outcome=outcome.to_dict(),
        receipt_ref="receipt-reuse", artifact_refs=(), node_execution_id="node-reuse",
        checkpoint_ns="react", checkpoint_id="reuse", expected_continuation_version=1,
        continuation_payload={"pending": False},
        event=RunEventCandidate(
            event_key="effect:reuse", kind="tool.outcome",
            status=OutcomeStatus.SUCCEEDED, driver_kind="react",
        ),
    )
    driver = RecordingSignalDriver()
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=4), query=lambda ref, actor: uow.query(ref, actor),
        finalize=lambda *args, **kwargs: None,
        tool_executor=UnifiedToolExecutor(ReuseOnlyRegistry()),
    )
    await runtime.consume_candidate(
        RegisteredDriver("react", driver, durable_from_start=True), record,
        ExecuteTools(
            run_id, "command-reuse", (call,), (tool_context,), (0,),
            effectful=(True,),
        ),
    )
    assert driver.last_signal.outcomes == (outcome,)
    assert driver.last_signal.statuses == (OutcomeStatus.SUCCEEDED,)


@pytest.mark.asyncio
async def test_in_flight_effect_does_not_starve_new_effect_in_same_batch(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    run_id = "interleave-run"
    context = RunContext(
        session_id="s1", root_run_id=run_id, parent_run_id=None,
        request_id="request-interleave", turn_id="turn-interleave", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="trace-interleave", principal_id="principal-s1", auth_epoch=1,
    )
    record = (await uow.create(RunCreate(
        run_id=run_id, idempotency_key="root:s1:request-interleave:turn-interleave",
        context=context, payload_fingerprint=fingerprint_json({"batch": True}),
        capability_fingerprint=context.capability_hash, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.DURABLE,
    ))).record
    calls = tuple(
        PreparedToolCall.prepare(
            tool_name=f"write-{name}", stable_call_id=f"call-{name}",
            final_params={"name": name}, tool_spec_version="v1", schema_hash="schema",
            permission_policy_version="v1", effect_type="opaque_manual",
            effect_policy_version="v1",
        )
        for name in ("in-flight", "new")
    )
    contexts = tuple(
        ToolExecutionContext(
            scope_id="scope", session_id="s1", request_id=context.request_id,
            root_run_id=run_id, run_id=run_id, call_id=call.stable_call_id,
            effect_id=f"effect-{index}", capability_hash=context.capability_hash,
            scope_hash="scope",
        )
        for index, call in enumerate(calls, 1)
    )
    await uow.consume_grant_and_claim_effect(
        None, host().actor(root_run_id=run_id), run_id=run_id,
        expected_session_id="s1", call_id=calls[0].stable_call_id,
        effect_id=contexts[0].effect_id, tool_name=calls[0].tool_name,
        args_hash=calls[0].args_hash, capability_hash=contexts[0].capability_hash,
        scope_hash=contexts[0].scope_hash, effect_type=calls[0].effect_type,
        policy={"kind": calls[0].effect_type, "version": calls[0].effect_policy_version},
        prepared=calls[0].to_dict(), worker_owner="kernel", worker_epoch=1,
    )
    registry, driver = InterleavingRegistry(), RecordingSignalDriver()
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=4),
        query=lambda ref, actor: uow.query(ref, actor),
        finalize=lambda *args, **kwargs: None,
        tool_executor=UnifiedToolExecutor(registry),
    )
    await runtime.consume_candidate(
        RegisteredDriver("react", driver, durable_from_start=True), record,
        ExecuteTools(
            run_id, "command-interleave", calls, contexts, (3, 7),
            effectful=(True, True),
        ),
    )
    assert registry.physical_calls == ["call-new"]
    assert registry.acknowledged == []
    assert driver.last_signal.original_indexes == (7,)
    assert driver.last_signal.outcomes[0].value["call_id"] == "call-new"


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


def test_driver_port_has_one_tagged_event_and_one_tagged_signal() -> None:
    source = Path("backend/deskpet/harness/ports.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    legacy_types = {
        "TokenCandidate", "ProviderFallbackCandidate", "ExecuteTools",
        "OpenDecision", "DelegateRun", "DriverTerminalCandidate",
        "ChildAcceptedCandidate", "CancelAcknowledgedCandidate",
        "PersistedEventCandidate", "ToolOutcomesSignal", "DecisionSignal",
        "ChildAcceptedSignal", "ChildTerminalSignal", "DriverCandidate",
    }
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert classes.isdisjoint(legacy_types)
    assert {"DriverEvent", "DriverSignal"} <= classes
    assert isinstance(TokenCandidate("run", "ok"), DriverEvent)
    assert isinstance(ChildAcceptedSignal("run", "command", "child"), DriverSignal)
