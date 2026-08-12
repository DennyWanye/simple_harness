from __future__ import annotations

import asyncio
import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints

import pytest
import pytest_asyncio
import aiosqlite

from deskpet.agent.task_work_context import TaskWorkContextResolver
from deskpet.execution.contracts import (
    ActorContext,
    AuthorizationError,
    DecisionOpen,
    DecisionStatus,
    AttachmentPolicy,
    IdempotencyConflict,
    OutcomeStatus,
    PersistenceLevel,
    RecoveryLease,
    RunContext,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunRef,
    RunStatus,
    StaleRecoveryLease,
    TERMINAL_RUN_STATUSES,
    WorkflowRunSeed,
    fingerprint_json,
)
from deskpet.execution.provider_fault_script import (
    ProviderFaultInjectedError,
    ProviderFaultScriptV1,
)
from deskpet.execution.provider_invocations import ProviderInvocationCoordinator
from deskpet.execution.tool_completion_latch_script import (
    ToolCompletionLatchScriptV1,
)
from deskpet.harness.context import HostContextFactory
from deskpet.harness.bootstrap import HarnessRuntime
from deskpet.harness.contracts import driver_catalog
from deskpet.harness.drivers.react import (
    ReActDriver,
    ReactFinal,
    ReactToolBatch,
)
from deskpet.harness.child_runs import (
    ChildRunCoordinator,
)
from deskpet.harness.reconciler import HarnessReconciler as _HarnessReconciler
from deskpet.harness.kernel import (
    HostContext,
    RegisteredDriver,
    RunKernel,
    RunRequest,
    decision_response_allows,
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
    ToolOutcomesSignal,
    UserContinuationSignal,
)
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter
from deskpet.harness.tool_executor import EffectBatch, EffectBatchExecutor
from deskpet.harness.runtime import DriverRuntime
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.reconciler import HarnessReconciler
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import PreparedToolCallStale, ToolRegistry
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"decision": "deny"}, False),
        ({"decision": "DENIED"}, False),
        ({"decision": "reject"}, False),
        ({"resolution": "cancelled"}, False),
        ({"decision": "allow"}, True),
        ({"decision": "allow_session"}, True),
        ({"allow": False, "decision": "allow"}, False),
        ({"approved": False, "decision": "allow"}, False),
        ({"answer": "no"}, True),
    ],
)
def test_decision_response_allows_permission_and_workflow_vocabularies(
    response,
    expected,
) -> None:
    assert decision_response_allows(response) is expected


class ChildReconciler(_HarnessReconciler):
    def __init__(self, coordinator, kernel, *, owner):
        super().__init__(coordinator._store, kernel, coordinator=coordinator, owner=owner)

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


def _profiles(profile_key: str, driver_kind: str) -> ProfileRegistry:
    workflow = (
        {
            "workflow_key": profile_key,
            "workflow_name": "fixture",
            "workflow_version": "v1",
            "state_factory": dict,
            "context_factory": dict,
        }
        if driver_kind == "workflow"
        else {}
    )
    return ProfileRegistry((ProfileSpec(profile_key, profile_key, driver_kind, **workflow),))


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


class BlockingCancelableDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.anext_started = asyncio.Event()
        self.start_closed = asyncio.Event()
        self.start_is_running = False
        self.cancel_overlapped_start = False

    async def start(self, request):
        self.starts += 1
        self.start_is_running = True
        self.anext_started.set()
        try:
            await asyncio.Event().wait()
            yield TokenCandidate(request.run_id, "unreachable")
        finally:
            self.start_is_running = False
            self.start_closed.set()

    async def cancel(self, run_id, reason):
        self.cancel_overlapped_start = self.start_is_running
        async for candidate in super().cancel(run_id, reason):
            yield candidate


class BlockingCancelAcknowledgementDriver(BlockingCancelableDriver):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_started = asyncio.Event()
        self.release_cancel = asyncio.Event()

    async def cancel(self, run_id, reason):
        self.cancel_overlapped_start = self.start_is_running
        self.cancel_started.set()
        await self.release_cancel.wait()
        async for candidate in FakeDriver.cancel(self, run_id, reason):
            yield candidate


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


class QueuedContinuationDriver(FakeDriver):
    def __init__(self, uow) -> None:
        super().__init__()
        self.uow = uow
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.continuation_run_ids: list[str] = []

    async def start(self, request):
        self.starts += 1
        await self.uow.persist_react_boundary(
            request.run_id,
            0,
            {
                "messages": [{"role": "user", "content": "seed"}],
                "completion_state": {},
            },
        )
        self.started.set()
        await self.release.wait()
        yield DriverTerminalCandidate(
            request.run_id, "completed", "stale first answer"
        )

    async def signal(self, signal, recovery_lease=None):
        self.signals += 1
        assert signal.kind == "user_continuation"
        saved = await self.uow.load_continuation(signal.run_id)
        assert saved is not None
        payload = dict(saved.payload)
        messages = list(payload.get("messages") or ())
        messages.append(
            {
                "role": "user",
                "content": signal.content,
                "_deskpet_message_ref": signal.message_ref,
            }
        )
        state = dict(payload.get("completion_state") or {})
        state["pending_resume_signal"] = {
            "type": "user_continuation",
            "message_ref": signal.message_ref,
        }
        payload.update(messages=messages, completion_state=state)
        event = RunEventCandidate(
            event_key=f"user-continuation:{signal.message_ref}",
            kind="run.user_continuation",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="react",
        )
        _conversation, _saved, stored = (
            await self.uow.commit_queued_user_continuation(
                signal.run_id,
                signal.message_ref,
                task_scope_id=signal.task_scope_id,
                expected_continuation_version=saved.version,
                continuation_payload=payload,
                event=event,
            )
        )
        self.continuation_run_ids.append(signal.run_id)
        yield PersistedEventCandidate(stored)
        yield DriverTerminalCandidate(
            signal.run_id, "completed", "answer after steering"
        )


class BlockingQueuedContinuationDriver(QueuedContinuationDriver):
    def __init__(self, uow) -> None:
        super().__init__(uow)
        self.continuation_started = asyncio.Event()
        self.continuation_closed = asyncio.Event()

    async def signal(self, signal, recovery_lease=None):
        self.signals += 1
        assert signal.kind == "user_continuation"
        self.continuation_started.set()
        try:
            await asyncio.Event().wait()
            if False:
                yield TokenCandidate(signal.run_id, "unreachable")
        finally:
            self.continuation_closed.set()


class AtomicStartDriver(FakeDriver):
    def __init__(self, uow) -> None:
        super().__init__()
        self.uow = uow

    async def start(self, request):
        self.starts += 1
        assert request.run_context is not None
        spec = request.run_spec or RunCreate(
            run_id=request.run_id,
            idempotency_key=f"workflow:{request.run_id}",
            context=request.run_context,
            payload_fingerprint=fingerprint_json(dict(request.request_payload)),
            capability_fingerprint=request.run_context.capability_hash,
            driver_kind="workflow",
            profile_key=request.profile_key,
            persistence_level=PersistenceLevel.DURABLE,
        )
        capability_snapshot = next(
            candidate
            for candidate in (
                {},
                {"tools": list(request.capability_snapshot.get("capabilities", ()))},
                {"capabilities": list(request.capability_snapshot.get("capabilities", ()))},
            )
            if fingerprint_json(candidate) == spec.capability_fingerprint
        )
        await self.uow.start_workflow(
            spec,
            WorkflowRunSeed(
                request_key=spec.idempotency_key,
                workflow_name="fixture",
                workflow_version="v1",
                manifest_hash="m" * 64,
                implementation_hash="i" * 64,
                capability_hash=spec.capability_fingerprint,
                capability_snapshot=capability_snapshot,
                state_schema_version=1,
                trace_id=spec.context.trace_id,
                thread_id=request.run_id,
            ),
            accepted_event=RunEventCandidate(
                event_key="accepted",
                kind="workflow.accepted",
                status=OutcomeStatus.ACCEPTED,
                driver_kind="workflow",
            ),
        )
        accepted = (await self.uow.list_events(request.run_id))[-1]
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
        self.closed = False

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
        if self.closed:
            raise RuntimeError("resume after collaborator close")
        self.resumes.append((boundary, response))
        if False:
            yield ReactFinal("unreachable")

    async def cancel(self, run_id, reason):
        return None

    async def close(self):
        self.closed = True


class MixedLateRegistry:
    def __init__(self) -> None:
        self.outcomes = {}
        self.metadata = {}
        self.contexts = {}
        self.ready_effects = set()
        self.block_late_receipt = False
        self.late_receipt_started = asyncio.Event()
        self.release_late_receipt = asyncio.Event()
        self.prepared_ready_callback = None

    def _set_prepared_ready_callback(self, callback):
        self.prepared_ready_callback = callback

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
        self.ready_effects.discard(effect_id)
        self.outcomes.pop(effect_id, None)
        self.metadata.pop(effect_id, None)

    async def observe_late_prepared(self, effect_id):
        outcome = self.outcomes.get(effect_id)
        if effect_id in self.ready_effects:
            self.late_receipt_started.set()
            if self.block_late_receipt:
                await self.release_late_receipt.wait()
        return ("complete", outcome) if outcome is not None else ("pending", None)

    def ready_late_prepared_run_ids(self):
        return frozenset(
            self.contexts[effect_id].run_id for effect_id in self.ready_effects
        )

    async def close_prepared_executions(self, timeout):
        deadline = asyncio.get_running_loop().time() + timeout
        while "effect-2" not in self.outcomes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.001)

    def complete_late(self) -> None:
        self.outcomes["effect-2"] = NormalizedToolOutcome.success({"written": 2})
        self.ready_effects.add("effect-2")
        self.metadata["effect-2"] = {
            "receipt_ref": "receipt-late", "evidence_verified": True,
            "outcome_status": "succeeded",
        }
        if self.prepared_ready_callback is not None:
            self.prepared_ready_callback(self.contexts["effect-2"].run_id)


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


async def _single_effect_fixture(tmp_path, registry, driver):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    run_id = "effect-runtime-run"
    context = RunContext(
        session_id="s1", root_run_id=run_id, parent_run_id=None,
        request_id="request-effect", turn_id="turn-effect", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="trace-effect", principal_id="principal-s1", auth_epoch=1,
    )
    record = (await uow.create(RunCreate(
        run_id=run_id, idempotency_key="root:s1:request-effect:turn-effect",
        context=context, payload_fingerprint=fingerprint_json({"effect": True}),
        capability_fingerprint=context.capability_hash, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.DURABLE,
    ))).record
    call = PreparedToolCall.prepare(
        tool_name="write-ready", stable_call_id="call-effect",
        final_params={"value": 1}, tool_spec_version="v1", schema_hash="schema",
        permission_policy_version="v1", effect_type="opaque_manual",
        effect_policy_version="v1",
    )
    tool_context = ToolExecutionContext(
        scope_id="scope", session_id="s1", request_id=context.request_id,
        root_run_id=run_id, run_id=run_id, call_id=call.stable_call_id,
        effect_id="effect-ready", capability_hash=context.capability_hash,
        scope_hash="scope",
    )
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=4),
        query=lambda ref, actor: uow.query(ref, actor),
        finalize=lambda *args, **kwargs: None,
        tool_executor=EffectBatchExecutor(uow, registry),
    )
    command = ExecuteTools(
        run_id, "command-effect", (call,), (tool_context,), (5,), effectful=(True,)
    )
    return uow, record, runtime, RegisteredDriver("react", driver, durable_from_start=True), command


def test_host_context_freezes_exact_provider_model_bindings() -> None:
    context = HostContextFactory().create_run_context(
        session_id="session-model",
        root_run_id="run-model",
        request_id="request-model",
        turn_id="turn-model",
        venue="text",
        capability_hash="c" * 64,
        provider_plan=("relay-cloud",),
        provider_bindings=(("relay-cloud", "kimi-k3"),),
        trace_id="trace-model",
        principal_id="principal-model",
    )

    assert context.provider_plan == {
        "providers": ("relay-cloud",),
        "bindings": (
            {"provider_id": "relay-cloud", "model_id": "kimi-k3"},
        ),
    }


@pytest.mark.asyncio
async def test_terminal_child_commit_wakes_parent_signal_reconciliation(tmp_path) -> None:
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.initialize()
    parent_context = RunContext(
        session_id="session-child-terminal",
        root_run_id="parent-run",
        parent_run_id=None,
        request_id="request-parent",
        turn_id="turn-child",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-parent",
        principal_id="principal-child",
        auth_epoch=1,
    )
    await store.create(
        RunCreate(
            run_id="parent-run",
            idempotency_key="root:test-terminal-trigger",
            context=parent_context,
            payload_fingerprint=fingerprint_json({"parent": True}),
            capability_fingerprint=parent_context.capability_hash,
            driver_kind="react",
            profile_key="agent.general",
            persistence_level=PersistenceLevel.DURABLE,
        )
    )
    context = RunContext(
        session_id="session-child-terminal",
        root_run_id="parent-run",
        parent_run_id="parent-run",
        request_id="request-child",
        turn_id="turn-child",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-child",
        principal_id="principal-child",
        auth_epoch=1,
    )
    record = (
        await store.create(
            RunCreate(
                run_id="child-run",
                idempotency_key="delegate:test-terminal-trigger",
                context=context,
                payload_fingerprint=fingerprint_json({"child": True}),
                capability_fingerprint=context.capability_hash,
                driver_kind="workflow",
                profile_key="workflow.durable_task",
                persistence_level=PersistenceLevel.DURABLE,
            )
        )
    ).record
    authoritative = replace(
        record,
        status=RunStatus.FAILED,
        terminal_event_id="terminal-child-run",
        ended_at=2.0,
        updated_at=2.0,
    )

    class Uow:
        async def query(self, ref, actor):
            return authoritative

    triggered: list[str] = []
    child_runs = ChildRunCoordinator(store)
    child_runs.bind_reconcile_trigger(lambda: triggered.append("terminal"))

    async def finalize(*args, **kwargs):
        return RunEvent(
            event_id="terminal-child-run",
            run_id=record.run_id,
            root_run_id=record.context.root_run_id,
            session_id=record.context.session_id,
            durable_seq=1,
            live_cursor=None,
            candidate=RunEventCandidate(
                event_key="terminal:child-run",
                kind="run.final",
                status=OutcomeStatus.FAILED,
                driver_kind="workflow",
            ),
            created_at=2.0,
        )

    runtime = DriverRuntime(
        uow=Uow(),
        live=BoundedLiveIndex(max_runs=2),
        query=lambda ref, actor: Uow().query(ref, actor),
        finalize=finalize,
        child_runs=child_runs,
    )
    await runtime.commit_terminal(
        record,
        "workflow",
        DriverTerminalCandidate(record.run_id, "failed", error="provider failed"),
        current=record,
    )

    assert triggered == ["terminal"]


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


class CrashOnDecisionResume(ClarificationCollaborator):
    async def resume(self, boundary, response):
        raise RuntimeError("crash after atomic decision commit")
        yield ReactFinal("unreachable")


@pytest_asyncio.fixture
async def kernel(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    classifier = StaticClassifier("react.default")
    driver = FakeDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(classifier, _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )
    return value, classifier, driver


def host(session: str = "s1", *, capability_hash: str = "c" * 64) -> HostContext:
    return HostContext(
        session_id=session,
        principal_id=f"principal-{session}",
        auth_epoch=1,
        capability_hash=capability_hash,
        available_capabilities=frozenset(),
        provider_plan=("primary",),
        trace_id="trace-1",
    )


def _live_run(kernel: RunKernel, run_id: str):
    active = kernel._live.get(run_id)
    assert active is not None
    return active


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
async def test_new_signal_clears_recovery_deferral_before_and_after_dispatch(
    kernel,
) -> None:
    value, _, _ = kernel
    handle = await value.start(
        RunRequest("hello", "req-clear-deferral", "turn-clear-deferral"),
        host(),
    )
    actor = host().actor(root_run_id=handle.root_run_id)
    active = _live_run(value, handle.ref.run_id)
    active.recovery_deferred_until = (
        asyncio.get_running_loop().time() + 30.0
    )

    receipt = await value.signal(
        handle.ref,
        actor,
        DriverSignal(
            handle.ref.run_id,
            "child_accepted",
            {
                "command_id": "command-1",
                "child_run_id": "child-1",
                "signal_id": "signal-1",
            },
        ),
    )

    assert receipt.accepted is True
    assert active.recovery_deferred_until == 0.0


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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", ImmediateTerminalDriver()),)),
        max_live_runs=4,
    )
    handle = await value.start(RunRequest("hello", "req-live", "turn-live"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    assert [event async for event in value.observe(handle.ref, actor)][-1].kind == "run.final"
    async with aiosqlite.connect(path) as db:
        rows = (await (await db.execute("SELECT COUNT(*) FROM execution_runs")).fetchone())[0]
    assert rows == 0
    active = _live_run(value, handle.ref.run_id)
    assert active.task is None or active.task.done()
    assert (active.driver_state, active.driver_iterator) == (None, None)
    assert not active.subscribers
    await value.close(handle.ref, actor)
    assert value._live.get(handle.ref.run_id) is None


@pytest.mark.asyncio
async def test_observe_cannot_miss_terminal_between_snapshot_and_subscribe(
    tmp_path, monkeypatch,
) -> None:
    class TerminalBetweenReadsDriver(FakeDriver):
        def __init__(self) -> None:
            super().__init__()
            self.waiting = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def start(self, request):
            self.starts += 1
            yield TokenCandidate(request.run_id, "before terminal")
            self.waiting.set()
            await self.release.wait()
            try:
                yield DriverTerminalCandidate(
                    request.run_id, "completed", "after snapshot"
                )
            finally:
                self.finished.set()

    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = TerminalBetweenReadsDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )
    handle = await value.start(
        RunRequest("race", "req-observe-race", "turn-observe-race"), host()
    )
    actor = host().actor(root_run_id=handle.root_run_id)
    await driver.waiting.wait()

    original_list_events = uow.list_events
    armed = True

    async def snapshot_then_finish(run_id, *, after_durable_seq=0):
        nonlocal armed
        snapshot = await original_list_events(
            run_id, after_durable_seq=after_durable_seq
        )
        if armed:
            armed = False
            driver.release.set()
            await driver.finished.wait()
        return snapshot

    monkeypatch.setattr(uow, "list_events", snapshot_then_finish)

    async def collect():
        return [event async for event in value.observe(handle.ref, actor)]

    events = await asyncio.wait_for(collect(), timeout=1.0)
    assert [event.kind for event in events] == ["transcript", "run.final"]
    assert not _live_run(value, handle.ref.run_id).subscribers


@pytest.mark.asyncio
async def test_close_keeps_running_task_indexed_until_it_finishes(tmp_path) -> None:
    class HoldingDriver(FakeDriver):
        def __init__(self):
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def start(self, request):
            self.starts += 1
            self.started.set()
            yield TokenCandidate(request.run_id, "started")
            await self.release.wait()

    driver = HoldingDriver()
    value = RunKernel(
        uow=SqliteExecutionUnitOfWork(tmp_path / "workflow.db"),
        router=RegisteredRouter(
            StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )
    await value._uow.initialize()
    handle = await value.start(RunRequest("hold", "req-close", "turn-close"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    await driver.started.wait()
    task = _live_run(value, handle.ref.run_id).task
    await value.close(handle.ref, actor)
    assert _live_run(value, handle.ref.run_id).task is task and not task.done()
    driver.release.set()
    await task
    await value.close(handle.ref, actor)
    assert value._live.get(handle.ref.run_id) is None


@pytest.mark.asyncio
async def test_running_root_queues_continuation_and_supersedes_stale_terminal(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.activate_runtime()
    driver = QueuedContinuationDriver(uow)
    continuation_updates = []

    async def observe_continuation(record, signal, status, error):
        continuation_updates.append(
            (record.run_id, signal.message_ref, status, error)
        )

    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
        continuation_observer=observe_continuation,
    )
    request = RunRequest(
        "long task", "req-running-continuation", "turn-running-continuation"
    )
    handle = await value.start(request, host())
    actor = host().actor(root_run_id=handle.root_run_id)
    await driver.started.wait()

    resolver = TaskWorkContextResolver(tmp_path / "tasks")
    task_scope_id = resolver.task_scope_id(
        "s1", request.request_id, request.turn_id
    )
    work = resolver.resolve(
        session_id="s1",
        root_run_id=handle.ref.run_id,
        task_scope_id=task_scope_id,
        explicit_workspace=tmp_path / "tasks" / "running",
    )
    conversation = resolver.conversation_boundary(
        work, ("request:seed",)
    )
    await uow.create_task_context(
        work, conversation, resolver.projection(work)
    )
    active = _live_run(value, handle.ref.run_id)
    active.recovery_deferred_until = (
        asyncio.get_running_loop().time() + 30.0
    )

    receipt = await value.signal(
        handle.ref,
        actor,
        UserContinuationSignal(
            handle.ref.run_id,
            task_scope_id=task_scope_id,
            message_ref="request:steer-running",
            content="change direction without creating another root",
            expected_boundary_version=conversation.version,
        ),
    )

    assert receipt.accepted is True
    assert receipt.reason == "continuation_queued"
    assert active.recovery_deferred_until == 0.0
    assert driver.continuation_run_ids == []
    driver.release.set()
    assert active.task is not None
    await active.task

    durable = await uow.query(handle.ref, actor)
    queued = await uow.get_user_continuation(
        handle.ref.run_id, "request:steer-running"
    )
    assert durable.status is RunStatus.COMPLETED
    assert queued is not None and queued.status == "bound"
    assert driver.continuation_run_ids == [handle.ref.run_id]
    assert continuation_updates == [
        (
            handle.ref.run_id,
            "request:steer-running",
            "bound",
            None,
        )
    ]
    events = await uow.list_events(handle.ref.run_id)
    assert [event.kind for event in events].count("run.final") == 1
    assert events[-1].candidate.payload["text"] == "answer after steering"
    async with aiosqlite.connect(path) as db:
        root_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM execution_runs WHERE parent_run_id IS NULL"
                )
            ).fetchone()
        )[0]
    assert root_count == 1


@pytest.mark.asyncio
async def test_terminal_enqueue_race_retries_fifo_after_run_version_fence(
    tmp_path, monkeypatch
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.activate_runtime()
    driver = QueuedContinuationDriver(uow)
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )
    request = RunRequest("race", "req-race", "turn-race")
    handle = await value.start(request, host())
    actor = host().actor(root_run_id=handle.root_run_id)
    await driver.started.wait()
    resolver = TaskWorkContextResolver(tmp_path / "tasks")
    scope = resolver.task_scope_id("s1", request.request_id, request.turn_id)
    work = resolver.resolve(
        session_id="s1",
        root_run_id=handle.ref.run_id,
        task_scope_id=scope,
        explicit_workspace=tmp_path / "tasks" / "race",
    )
    conversation = resolver.conversation_boundary(
        work, ("request:seed",)
    )
    await uow.create_task_context(
        work, conversation, resolver.projection(work)
    )

    original_get_next = uow.get_next_user_continuation
    terminal_saw_empty = asyncio.Event()
    allow_terminal_cas = asyncio.Event()
    paused = False

    async def pause_first_empty_read(run_id):
        nonlocal paused
        result = await original_get_next(run_id)
        if result is None and not paused:
            paused = True
            terminal_saw_empty.set()
            await allow_terminal_cas.wait()
        return result

    monkeypatch.setattr(
        uow, "get_next_user_continuation", pause_first_empty_read
    )
    driver.release.set()
    await terminal_saw_empty.wait()
    receipt = await value.signal(
        handle.ref,
        actor,
        UserContinuationSignal(
            handle.ref.run_id,
            task_scope_id=scope,
            message_ref="request:race-steer",
            content="win the terminal race",
            expected_boundary_version=conversation.version,
        ),
    )
    allow_terminal_cas.set()
    active = _live_run(value, handle.ref.run_id)
    assert active.task is not None
    await active.task

    durable = await uow.query(handle.ref, actor)
    assert receipt.accepted is True
    assert durable.status is RunStatus.COMPLETED
    assert driver.continuation_run_ids == [handle.ref.run_id]
    assert (
        await uow.get_user_continuation(
            handle.ref.run_id, "request:race-steer"
        )
    ).status == "bound"


@pytest.mark.asyncio
async def test_durable_terminal_hydrates_live_record_for_capacity_eviction(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()

    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver(
            "react", ImmediateTerminalDriver(), durable_from_start=True),)),
        max_live_runs=1,
    )
    first = await value.start(RunRequest("one", "req-cap-1", "turn-cap-1"), host())
    actor = host().actor(root_run_id=first.root_run_id)
    assert [event async for event in value.observe(first.ref, actor)][-1].kind == "run.final"
    assert _live_run(value, first.ref.run_id).record.status is RunStatus.COMPLETED
    second = await value.start(RunRequest("two", "req-cap-2", "turn-cap-2"), host())
    assert value._live.get(first.ref.run_id) is None
    assert value._live.get(second.ref.run_id) is not None
    assert [event async for event in value.observe(
        second.ref, host().actor(root_run_id=second.root_run_id))][-1].candidate.is_terminal


@pytest.mark.asyncio
async def test_durable_terminal_observe_and_close_reuse_immutable_live_record(
    tmp_path, monkeypatch,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()

    async def unexpected_child_lookup(*args, **kwargs):
        raise AssertionError("root terminal must not query child command ownership")

    monkeypatch.setattr(uow, "get_child_command_for_run", unexpected_child_lookup)
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver(
            "react", ImmediateTerminalDriver(), durable_from_start=True),)),
    )
    handle = await value.start(RunRequest("one", "req-cache", "turn-cache"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    task = _live_run(value, handle.ref.run_id).task
    assert task is not None
    await task
    assert _live_run(value, handle.ref.run_id).record.status is RunStatus.COMPLETED

    async def unexpected_query(*args, **kwargs):
        raise AssertionError("terminal LiveRun must not re-query immutable run state")

    monkeypatch.setattr(uow, "query", unexpected_query)
    assert [event async for event in value.observe(handle.ref, actor)][-1].kind == "run.final"
    await value.close(handle.ref, actor)
    assert value._live.get(handle.ref.run_id) is None


@pytest.mark.asyncio
async def test_first_decision_atomically_promotes_boundary_and_kernel_adopts_uow(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.activate_runtime()
    driver = ReActDriver(ClarificationCollaborator(), uow, NoEffects())
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
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
    assert _live_run(value, handle.ref.run_id).record is None

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
    assert [event async for event in stream][-1].kind == "run.final"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_point",
    ["decision_resolve_after_cas", "decision_resolve_after_boundary", "decision_resolve_before_commit"],
)
async def test_kernel_decision_boundary_rolls_back_and_retries_after_restart(
    tmp_path, crash_point,
) -> None:
    path = tmp_path / "workflow.db"
    crashing_uow = SqliteExecutionUnitOfWork(
        path,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == crash_point else None,
    )
    await crashing_uow.activate_runtime()
    first = RunKernel(
        uow=crashing_uow,
        router=RegisteredRouter(StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", ReActDriver(ClarificationCollaborator(), crashing_uow, NoEffects())),)),
    )
    handle = await first.start(RunRequest("clarify", f"req-{crash_point}", "turn-atomic"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    stream = first.observe(handle.ref, actor)
    assert (await anext(stream)).kind == "decision"
    before = await crashing_uow.load_continuation(handle.ref.run_id)

    with pytest.raises(RuntimeError, match=f"crash:{crash_point}"):
        await first.signal(
            handle.ref, actor,
            DecisionSignal(handle.ref.run_id, "clarify-1", {"answer": "yes"}, "clarify-nonce", 0),
        )

    unchanged = await crashing_uow.load_continuation(handle.ref.run_id)
    decision = await crashing_uow.get_decision("clarify-1", ref=handle.ref, actor=actor)
    assert unchanged is not None and before is not None
    assert unchanged.version == before.version
    assert unchanged.pending_decision_id == "clarify-1"
    assert decision.status is DecisionStatus.OPEN
    assert not any(event.kind == "run.resumed" for event in await crashing_uow.list_events(handle.ref.run_id))

    restarted_uow = SqliteExecutionUnitOfWork(path)
    await restarted_uow.initialize()
    restarted = RunKernel(
        uow=restarted_uow,
        router=RegisteredRouter(StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", ReActDriver(ClarificationCollaborator(), restarted_uow, NoEffects())),)),
    )
    assert (await restarted.signal(
        handle.ref, actor,
        DecisionSignal(handle.ref.run_id, "clarify-1", {"answer": "yes"}, "clarify-nonce", 0),
    )).accepted
    assert (await restarted_uow.query(handle.ref, actor)).status is RunStatus.COMPLETED
    events = await restarted_uow.list_events(handle.ref.run_id)
    assert sum(event.kind == "run.resumed" for event in events) == 1


@pytest.mark.asyncio
async def test_kernel_decision_rejects_wrong_stale_and_duplicate_without_advancing(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.activate_runtime()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", ReActDriver(ClarificationCollaborator(), uow, NoEffects())),)),
    )
    handle = await kernel.start(RunRequest("clarify", "req-fences", "turn-fences"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    stream = kernel.observe(handle.ref, actor)
    await anext(stream)
    waiting = await uow.load_continuation(handle.ref.run_id)
    for nonce, version in (("wrong", 0), ("clarify-nonce", 7)):
        with pytest.raises(Exception):
            await kernel.signal(
                handle.ref, actor,
                DecisionSignal(handle.ref.run_id, "clarify-1", {"answer": "yes"}, nonce, version),
            )
        current = await uow.load_continuation(handle.ref.run_id)
        assert current is not None and waiting is not None and current.version == waiting.version
        assert (await uow.get_decision("clarify-1", ref=handle.ref, actor=actor)).status is DecisionStatus.OPEN
    assert (await kernel.signal(
        handle.ref, actor,
        DecisionSignal(handle.ref.run_id, "clarify-1", {"answer": "yes"}, "clarify-nonce", 0),
    )).accepted
    settled = await uow.load_continuation(handle.ref.run_id)
    replay = await kernel.signal(
        handle.ref, actor,
        DecisionSignal(handle.ref.run_id, "clarify-1", {"answer": "yes"}, "clarify-nonce", 0),
    )
    assert replay.accepted and replay.duplicate
    with pytest.raises(Exception):
        await kernel.signal(
            handle.ref, actor,
            DecisionSignal(handle.ref.run_id, "clarify-1", {"answer": "different"}, "clarify-nonce", 0),
        )
    duplicate = await uow.load_continuation(handle.ref.run_id)
    assert duplicate is not None and settled is not None and duplicate.version == settled.version


@pytest.mark.asyncio
async def test_recovery_resumes_committed_decision_marker_after_process_crash(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.activate_runtime()
    first = RunKernel(
        uow=uow,
        router=RegisteredRouter(StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", ReActDriver(CrashOnDecisionResume(), uow, NoEffects())),)),
    )
    handle = await first.start(RunRequest("clarify", "req-after-commit", "turn-after-commit"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    stream = first.observe(handle.ref, actor)
    await anext(stream)
    with pytest.raises(RuntimeError, match="after atomic decision commit"):
        await first.signal(
            handle.ref, actor,
            DecisionSignal(handle.ref.run_id, "clarify-1", {"answer": "yes"}, "clarify-nonce", 0),
        )
    committed = await uow.load_continuation(handle.ref.run_id)
    assert committed is not None and committed.pending_decision_id is None
    assert "pending_resume_signal" in committed.payload["completion_state"]

    restarted_uow = SqliteExecutionUnitOfWork(path)
    await restarted_uow.initialize()
    restarted = RunKernel(
        uow=restarted_uow,
        router=RegisteredRouter(StaticClassifier("react.default"), _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", ReActDriver(ClarificationCollaborator(), restarted_uow, NoEffects())),)),
    )
    assert restarted._live.get(handle.ref.run_id) is None
    await restarted.recover(handle.ref, actor)
    await _live_run(restarted, handle.ref.run_id).task
    assert (await restarted_uow.query(handle.ref, actor)).status is RunStatus.COMPLETED
    assert sum(event.kind == "run.resumed" for event in await restarted_uow.list_events(handle.ref.run_id)) == 1


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
async def test_cancel_interrupts_active_driver_owner_before_driver_cancel(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = BlockingCancelableDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )
    handle = await value.start(
        RunRequest("wait forever", "req-cancel-active", "turn-1"), host()
    )
    await asyncio.wait_for(driver.anext_started.wait(), timeout=1)

    cancelled = await asyncio.wait_for(
        value.cancel(
            handle.ref,
            host().actor(root_run_id=handle.root_run_id),
            "user_stop",
        ),
        timeout=1,
    )

    assert cancelled.acknowledged is True
    assert cancelled.status is RunStatus.CANCELLED
    assert driver.start_closed.is_set()
    assert driver.cancel_overlapped_start is False
    assert driver.cancelled == ["user_stop"]
    await value.close(handle.ref, host().actor(root_run_id=handle.root_run_id))


@pytest.mark.asyncio
async def test_cancel_waits_for_inflight_continuation_before_failing_fifo(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.activate_runtime()
    driver = BlockingQueuedContinuationDriver(uow)
    continuation_updates = []

    async def observe_continuation(record, signal, status, error):
        continuation_updates.append(
            (record.run_id, signal.message_ref, status, error)
        )

    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
        continuation_observer=observe_continuation,
    )
    request = RunRequest(
        "long task", "req-cancel-continuation", "turn-cancel-continuation"
    )
    handle = await value.start(request, host())
    actor = host().actor(root_run_id=handle.root_run_id)
    await driver.started.wait()
    resolver = TaskWorkContextResolver(tmp_path / "tasks")
    task_scope_id = resolver.task_scope_id(
        "s1", request.request_id, request.turn_id
    )
    work = resolver.resolve(
        session_id="s1",
        root_run_id=handle.ref.run_id,
        task_scope_id=task_scope_id,
        explicit_workspace=tmp_path / "tasks" / "cancel-continuation",
    )
    conversation = resolver.conversation_boundary(work, ("request:seed",))
    await uow.create_task_context(
        work, conversation, resolver.projection(work)
    )
    receipt = await value.signal(
        handle.ref,
        actor,
        UserContinuationSignal(
            handle.ref.run_id,
            task_scope_id=task_scope_id,
            message_ref="request:cancel-steer",
            content="steer immediately before cancellation",
            expected_boundary_version=conversation.version,
        ),
    )
    assert receipt.reason == "continuation_queued"
    driver.release.set()
    await asyncio.wait_for(driver.continuation_started.wait(), timeout=1)

    cancelled = await asyncio.wait_for(
        value.cancel(handle.ref, actor, "user_stop"), timeout=1
    )

    queued = await uow.get_user_continuation(
        handle.ref.run_id, "request:cancel-steer"
    )
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.acknowledged is True
    assert driver.continuation_closed.is_set()
    assert queued is not None
    assert queued.status == "failed"
    assert queued.error == "run_cancelled"
    assert continuation_updates == [
        (
            handle.ref.run_id,
            "request:cancel-steer",
            "failed",
            "run_cancelled",
        )
    ]


@pytest.mark.asyncio
async def test_cancel_retries_when_continuation_reservation_wins_version_race(
    tmp_path, monkeypatch
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.activate_runtime()
    driver = QueuedContinuationDriver(uow)
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )
    request = RunRequest("long task", "req-cancel-race", "turn-cancel-race")
    handle = await value.start(request, host())
    actor = host().actor(root_run_id=handle.root_run_id)
    await driver.started.wait()
    resolver = TaskWorkContextResolver(tmp_path / "tasks")
    task_scope_id = resolver.task_scope_id(
        "s1", request.request_id, request.turn_id
    )
    work = resolver.resolve(
        session_id="s1",
        root_run_id=handle.ref.run_id,
        task_scope_id=task_scope_id,
        explicit_workspace=tmp_path / "tasks" / "cancel-race",
    )
    conversation = resolver.conversation_boundary(work, ("request:seed",))
    await uow.create_task_context(
        work, conversation, resolver.projection(work)
    )
    cancel_read_old_version = asyncio.Event()
    allow_cancel_cas = asyncio.Event()
    original_request_cancel = value._request_cancel
    paused = False

    async def pause_first_cancel(record, **kwargs):
        nonlocal paused
        if not paused:
            paused = True
            cancel_read_old_version.set()
            await allow_cancel_cas.wait()
        return await original_request_cancel(record, **kwargs)

    monkeypatch.setattr(value, "_request_cancel", pause_first_cancel)
    cancel_task = asyncio.create_task(
        value.cancel(handle.ref, actor, "user_stop")
    )
    await asyncio.wait_for(cancel_read_old_version.wait(), timeout=1)
    receipt = await value.signal(
        handle.ref,
        actor,
        UserContinuationSignal(
            handle.ref.run_id,
            task_scope_id=task_scope_id,
            message_ref="request:cancel-race",
            content="reserve immediately before cancel CAS",
            expected_boundary_version=conversation.version,
        ),
    )
    allow_cancel_cas.set()
    cancelled = await asyncio.wait_for(cancel_task, timeout=1)

    queued = await uow.get_user_continuation(
        handle.ref.run_id, "request:cancel-race"
    )
    assert receipt.reason == "continuation_queued"
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.acknowledged is True
    assert queued is not None and queued.status == "failed"


@pytest.mark.asyncio
async def test_recover_cannot_install_owner_while_cancel_is_converging(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = BlockingCancelAcknowledgementDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )
    handle = await value.start(
        RunRequest("wait forever", "req-cancel-recover", "turn-1"), host()
    )
    actor = host().actor(root_run_id=handle.root_run_id)
    await driver.anext_started.wait()
    cancel_task = asyncio.create_task(
        value.cancel(handle.ref, actor, "user_stop")
    )
    await asyncio.wait_for(driver.cancel_started.wait(), timeout=1)

    recover_task = asyncio.create_task(value.recover(handle.ref, actor))
    await asyncio.sleep(0)
    assert recover_task.done() is False
    assert _live_run(value, handle.ref.run_id).task is None

    driver.release_cancel.set()
    cancelled = await asyncio.wait_for(cancel_task, timeout=1)
    await asyncio.wait_for(recover_task, timeout=1)
    assert cancelled.status is RunStatus.CANCELLED
    assert driver.recovers == 0
    active = _live_run(value, handle.ref.run_id)
    assert active.task is None or active.task.done()


@pytest.mark.asyncio
async def test_recover_joins_retiring_owner_before_relaunch(
    tmp_path, monkeypatch
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = FakeDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )
    retiring = asyncio.Event()
    release_retiring = asyncio.Event()
    original_drain = value._continuations._drain_owned

    async def pause_retiring_owner(ref, actor):
        retiring.set()
        await release_retiring.wait()
        await original_drain(ref, actor)

    monkeypatch.setattr(
        value._continuations, "_drain_owned", pause_retiring_owner
    )
    handle = await value.start(
        RunRequest("finish without terminal", "req-recover-join", "turn-1"),
        host(),
    )
    actor = host().actor(root_run_id=handle.root_run_id)
    await asyncio.wait_for(retiring.wait(), timeout=1)
    recover_task = asyncio.create_task(value.recover(handle.ref, actor))
    await asyncio.sleep(0)
    assert recover_task.done() is False
    assert driver.recovers == 0

    value._tool_executor = SimpleNamespace(
        ready_run_ids=lambda: frozenset({handle.ref.run_id})
    )
    release_retiring.set()
    await asyncio.wait_for(recover_task, timeout=1)
    active = _live_run(value, handle.ref.run_id)
    assert active.task is not None
    await asyncio.wait_for(active.task, timeout=1)
    assert driver.recovers == 1


@pytest.mark.asyncio
async def test_driver_failure_isolated_as_run_terminal(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", FailingDriver()),)),
    )
    handle = await value.start(RunRequest("hello", "req-fail", "turn-1"), host())
    stream = value.observe(
        handle.ref, host().actor(root_run_id=handle.root_run_id)
    )
    terminal = [event async for event in stream][-1]
    assert terminal.status is OutcomeStatus.FAILED
    active = _live_run(value, handle.ref.run_id)
    assert active.task is not None and active.task.done()
    assert active.subscribers == set()
    await value.close(
        handle.ref, host().actor(root_run_id=handle.root_run_id)
    )
    assert value._live.get(handle.ref.run_id) is None


@pytest.mark.asyncio
async def test_recovery_renews_before_driver_and_first_anext(tmp_path) -> None:
    timeline: list[str] = []

    class TrackingUow(SqliteExecutionUnitOfWork):
        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if lease_seconds is None:
                timeline.append("release")
            if not isinstance(subject, str) and lease_seconds is not None:
                timeline.append("renew")
            return await super().recovery_scope(
                subject, owner=owner, lease_seconds=lease_seconds
            )

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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", RecoveryOrderDriver(timeline)),)),
    )
    actor = host().actor(root_run_id="run-recovery-order")
    await value.recover(RunRef("run-recovery-order", "s1"), actor)
    await _live_run(value, "run-recovery-order").task

    assert timeline == ["renew", "recover", "renew", "anext", "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [RuntimeError, StaleRecoveryLease])
async def test_recovery_heartbeat_failure_closes_stream_and_release_does_not_mask(
    failure_type,
) -> None:
    class Uow:
        renewals = 0
        releases = 0

        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if lease_seconds is None:
                self.releases += 1
                raise RuntimeError("release cleanup failed")
            self.renewals += 1
            if self.renewals == 3:
                raise failure_type("heartbeat lost authority")
            return subject

    class Driver(FakeDriver):
        closed = False

        async def recover(self, run_id, recovery_lease):
            try:
                await asyncio.Event().wait()
                yield TokenCandidate(run_id, "unreachable")
            finally:
                self.closed = True

    uow, driver = Uow(), Driver()
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=1),
        query=lambda *args: None, finalize=lambda *args, **kwargs: None,
    )
    registration = RegisteredDriver("react", driver)
    record = SimpleNamespace(run_id="recovery-heartbeat")
    with pytest.raises(failure_type, match="heartbeat lost authority"):
        await runtime.consume_fenced(
            registration, record,
            lambda lease: driver.recover(record.run_id, lease),
            RecoveryLease(record.run_id, "owner", 1, 9999),
            heartbeat_interval=0.001, release=True,
        )
    assert driver.closed and uow.releases == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_code",
    [
        "prepared_call_stale",
        "tool_catalog_stale",
        "workflow_implementation_stale",
    ],
)
async def test_prepare_recovery_incompatible_snapshot_terminalizes_once(
    tmp_path, failure_code
) -> None:
    class IncompatibleSnapshotDriver(FakeDriver):
        async def prepare_recovery(self, run_id, recovery_lease):
            self.recovers += 1
            if failure_code == "prepared_call_stale":
                raise PreparedToolCallStale(
                    "prepared ToolSpec is no longer active or leased"
                )
            raise RuntimeError(failure_code)

    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    run_id = f"run-{failure_code}"
    context = RunContext(
        session_id="s1",
        root_run_id=run_id,
        parent_run_id=None,
        request_id=f"request-{failure_code}",
        turn_id=f"turn-{failure_code}",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-stale-catalog",
        principal_id="principal-s1",
        auth_epoch=1,
    )
    await uow.create(
        RunCreate(
            run_id=run_id,
            idempotency_key=f"root:s1:request-{failure_code}",
            context=context,
            payload_fingerprint=fingerprint_json({"text": "recover"}),
            capability_fingerprint=context.capability_hash,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
        )
    )
    driver = IncompatibleSnapshotDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )
    actor = host().actor(root_run_id=run_id)

    await kernel.recover(RunRef(run_id, "s1"), actor)

    terminal = await uow.query(RunRef(run_id, "s1"), actor)
    assert terminal.status is RunStatus.FAILED
    assert driver.recovers == 1

    # Supervisor ticks may ask again, but terminal runs are not relaunched.
    await kernel.recover(RunRef(run_id, "s1"), actor)
    assert driver.recovers == 1


def test_permanent_recovery_failure_unwraps_stale_prepared_call() -> None:
    direct = PreparedToolCallStale(
        "prepared ToolSpec is no longer active or leased"
    )
    try:
        raise direct
    except PreparedToolCallStale as cause:
        try:
            raise RuntimeError("outer cause") from cause
        except RuntimeError as wrapped_cause:
            assert (
                DriverRuntime._permanent_recovery_failure_code(wrapped_cause)
                == "prepared_call_stale"
            )
    try:
        raise direct
    except PreparedToolCallStale:
        try:
            raise RuntimeError("outer context")
        except RuntimeError as wrapped_context:
            assert (
                DriverRuntime._permanent_recovery_failure_code(wrapped_context)
                == "prepared_call_stale"
            )

    assert (
        DriverRuntime._permanent_recovery_failure_code(
            RuntimeError("temporary provider outage")
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("yield_before_failure", [False, True])
async def test_recover_stream_stale_prepared_call_terminalizes_once(
    tmp_path, yield_before_failure,
) -> None:
    class StalePreparedCallDriver(FakeDriver):
        async def recover(self, run_id, recovery_lease):
            self.recovers += 1
            if yield_before_failure:
                yield TokenCandidate(run_id, "partial recovery")
            raise PreparedToolCallStale(
                "prepared ToolSpec is no longer active or leased"
            )

    uow = SqliteExecutionUnitOfWork(
        tmp_path / f"stream-stale-{yield_before_failure}.db"
    )
    await uow.initialize()
    run_id = f"run-stream-stale-{yield_before_failure}"
    context = RunContext(
        session_id="s1",
        root_run_id=run_id,
        parent_run_id=None,
        request_id=f"request-stream-stale-{yield_before_failure}",
        turn_id=f"turn-stream-stale-{yield_before_failure}",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-stream-stale",
        principal_id="principal-s1",
        auth_epoch=1,
    )
    await uow.create(
        RunCreate(
            run_id=run_id,
            idempotency_key=f"root:s1:request-stream-stale-{yield_before_failure}",
            context=context,
            payload_fingerprint=fingerprint_json({"text": "recover"}),
            capability_fingerprint=context.capability_hash,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
        )
    )
    driver = StalePreparedCallDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )
    actor = host().actor(root_run_id=run_id)
    ref = RunRef(run_id, "s1")

    await kernel.recover(ref, actor)
    await _live_run(kernel, run_id).task

    terminal = await uow.query(ref, actor)
    assert terminal.status is RunStatus.FAILED
    assert driver.recovers == 1

    reconciler = HarnessReconciler(uow, kernel)
    for _ in range(3):
        assert await reconciler.recover_pending() == ()
    assert driver.recovers == 1
    events = await uow.list_events(run_id)
    assert len([event for event in events if event.kind == "run.final"]) == 1


@pytest.mark.asyncio
async def test_transient_recovery_failure_remains_retryable_without_terminal(
    tmp_path,
) -> None:
    class TransientRecoveryDriver(FakeDriver):
        async def recover(self, run_id, recovery_lease):
            self.recovers += 1
            raise RuntimeError("temporary provider outage")
            if False:
                yield TokenCandidate(run_id, "unreachable")

    uow = SqliteExecutionUnitOfWork(tmp_path / "transient-recovery.db")
    await uow.initialize()
    run_id = "run-transient-recovery"
    context = RunContext(
        session_id="s1",
        root_run_id=run_id,
        parent_run_id=None,
        request_id="request-transient-recovery",
        turn_id="turn-transient-recovery",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-transient-recovery",
        principal_id="principal-s1",
        auth_epoch=1,
    )
    await uow.create(
        RunCreate(
            run_id=run_id,
            idempotency_key="root:s1:request-transient-recovery",
            context=context,
            payload_fingerprint=fingerprint_json({"text": "recover"}),
            capability_fingerprint=context.capability_hash,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
        )
    )
    driver = TransientRecoveryDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )
    actor = host().actor(root_run_id=run_id)
    ref = RunRef(run_id, "s1")

    for expected_attempts in (1, 2):
        await kernel.recover(ref, actor)
        await asyncio.gather(
            _live_run(kernel, run_id).task,
            return_exceptions=True,
        )
        current = await uow.query(ref, actor)
        assert current.status not in TERMINAL_RUN_STATUSES
        assert driver.recovers == expected_attempts
        assert not [
            event
            for event in await uow.list_events(run_id)
            if event.kind == "run.final"
        ]


@pytest.mark.asyncio
async def test_recovery_cancel_closes_stream_and_releases_lease() -> None:
    class Uow:
        releases = 0

        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if lease_seconds is None:
                self.releases += 1
            return subject

    class Driver(FakeDriver):
        closed = False

        async def recover(self, run_id, recovery_lease):
            try:
                await asyncio.Event().wait()
                yield TokenCandidate(run_id, "unreachable")
            finally:
                self.closed = True

    uow, driver = Uow(), Driver()
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=1),
        query=lambda *args: None, finalize=lambda *args, **kwargs: None,
    )
    record = SimpleNamespace(run_id="cancelled-recovery")
    task = asyncio.create_task(runtime.consume_fenced(
        RegisteredDriver("react", driver), record,
        lambda lease: driver.recover(record.run_id, lease),
        RecoveryLease(record.run_id, "owner", 1, 9999),
        heartbeat_interval=0.001, release=True,
    ))
    await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert driver.closed and uow.releases == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_after_first", [False, True])
async def test_fenced_consumer_renews_between_cumulatively_long_short_candidates(
    stale_after_first,
) -> None:
    class Uow:
        renewals = 0

        async def recovery_scope(self, subject, **kwargs):
            self.renewals += 1
            if stale_after_first and self.renewals == 3:
                raise StaleRecoveryLease("lost between short candidates")
            return subject

    closed, consumed, elapsed = False, [], 0.0

    async def stream(_lease):
        nonlocal closed
        try:
            for index in range(3):
                yield SimpleNamespace(kind="token", index=index)
        finally:
            closed = True

    async def consume(_registration, _record, candidate, **kwargs):
        nonlocal elapsed
        elapsed += 0.004
        await asyncio.sleep(0)
        consumed.append(candidate.index)

    uow = Uow()
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=1),
        query=lambda *args: None, finalize=lambda *args, **kwargs: None,
    )
    runtime.consume_candidate = consume
    operation = runtime.consume_fenced(
        RegisteredDriver("react", FakeDriver()), SimpleNamespace(run_id="short"),
        stream, RecoveryLease("short", "owner", 1, 9999),
        heartbeat_interval=0.01,
    )
    if stale_after_first:
        with pytest.raises(StaleRecoveryLease, match="between short candidates"):
            await operation
        assert consumed == [0]
    else:
        await operation
        assert consumed == [0, 1, 2]
        assert elapsed > 0.01 and uow.renewals >= 5
    assert closed


@pytest.mark.asyncio
async def test_terminal_consume_stops_heartbeat_after_terminal_clears_lease() -> None:
    class Uow:
        cleared = False

        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if self.cleared and lease_seconds is not None:
                raise StaleRecoveryLease("terminal already released lease")
            return subject

    uow = Uow()
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=1),
        query=lambda *args: None, finalize=lambda *args, **kwargs: None,
    )

    async def consume_candidate(*args, **kwargs):
        uow.cleared = True
        await asyncio.sleep(0.01)
        return False

    runtime.consume_candidate = consume_candidate
    record = SimpleNamespace(run_id="terminal-recovery")

    async def terminal_stream(lease):
        yield DriverTerminalCandidate(record.run_id, "completed", "done")

    await runtime.consume_fenced(
        RegisteredDriver("react", FakeDriver()), record, terminal_stream,
        RecoveryLease(record.run_id, "owner", 1, 9999),
        heartbeat_interval=0.001,
    )


@pytest.mark.asyncio
async def test_decision_signal_is_durably_fenced_before_driver_resume(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    driver = FakeDriver()
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
    )
    handle = await value.start(RunRequest("hello", "req-decision", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    record = await uow.query(handle.ref, actor)
    await uow.commit_decision(
        DecisionOpen(
            decision_id="decision-1",
            run_id=handle.ref.run_id,
            nonce="nonce-1",
            kind="clarification",
            prompt_schema_version=1,
            prompt={"question": "continue?"},
            expires_at=None,
        ), actor, expected_run_version=record.version,
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
            _profiles("durable.default", "workflow"),
        ),
        drivers=driver_catalog((
            RegisteredDriver(
                "workflow",
                driver,
                durable_from_start=True,
                atomic_start=True,
            ),
        )),
    )
    capability_hash = fingerprint_json({"capabilities": []})
    handle = await value.start(
        RunRequest("long task", "req-atomic", "turn-1"),
        host(capability_hash=capability_hash),
    )
    actor = host(capability_hash=capability_hash).actor(root_run_id=handle.root_run_id)
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
            _profiles("durable.default", "workflow"),
        ),
        drivers=driver_catalog((
            RegisteredDriver(
                "workflow",
                AtomicStartDriver(uow),
                durable_from_start=True,
                atomic_start=True,
            ),
        )),
    )
    assert restarted._live.get(handle.ref.run_id) is None
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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
    )
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, DelegateRun(
        run_id=parent.run_id, command_id="terminal-replay",
        child_request={"task": "finish", "driver_kind": "react"},
        route_hint="react.default", capability_subset=(),
        attachment_policy=AttachmentPolicy.DETACHED,
        join_policy=JoinPolicy.DETACHED,
    ))
    failed = ChildReconciler(
        coordinator, kernel, owner="crashing-scheduler"
    )
    # Keep the scheduling lease comfortably alive through child startup so the
    # injected failure is the ack transaction itself, not incidental expiry.
    await failed.reconcile_once(lease_seconds=0.5)
    assert any("command ack" in error for error in failed.last_errors), failed.last_errors
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
        if kernel._live.get(command.child_run_id) is None:
            break
        await asyncio.sleep(0.01)
    assert kernel._live.get(command.child_run_id) is None

    await asyncio.sleep(0.55)
    async with kernel._lock:
        stale = kernel._live.add(command.child_run_id, actor)
        stale.record = child
    assert kernel._live.get(command.child_run_id) is not None
    fresh = SqliteExecutionUnitOfWork(path)
    await ChildReconciler(
        ChildRunCoordinator(fresh), kernel, owner="restarted-scheduler"
    ).reconcile_once(lease_seconds=1)

    assert (await fresh.get_child_command(command.operation_id)).status.value == "acked"
    assert driver.starts == 1
    assert kernel._live.get(command.child_run_id) is None
    assert await kernel._drain_active(1.0)
    await fresh.close()
    await uow.close()


@pytest.mark.asyncio
async def test_child_provider_fault_persists_failure_and_parent_recovers(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    parent_context = RunContext(
        session_id="s1", root_run_id="parent-provider-recovery",
        parent_run_id=None, request_id="request-parent-provider-recovery",
        turn_id="turn-parent-provider-recovery", venue="text", workspace={},
        capability_hash="c" * 64, provider_plan={},
        trace_id="trace-parent-provider-recovery", principal_id="p1",
        auth_epoch=0,
    )
    parent = (await uow.create(RunCreate(
        run_id=parent_context.root_run_id,
        idempotency_key="root:parent-provider-recovery",
        context=parent_context, payload_fingerprint="a" * 64,
        capability_fingerprint=parent_context.capability_hash,
        driver_kind="react", profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    ))).record
    await uow.persist_react_boundary(
        parent.run_id, 0, {"child_signals": []}
    )
    fault_path = tmp_path / "provider-faults.json"
    fault_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "rule_id": "child-provider-failure",
                        "injection_ref": "child-provider-failure-ref",
                        "session_id": "s1",
                        "workload_class": "main",
                        "callsite_id": "agent.root_turn",
                        "purpose": "agent_response",
                        "occurrence": 1,
                        "action": "child_provider_failure",
                        "run_kind": "child",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fault_script = ProviderFaultScriptV1.from_file(
        fault_path, environ={"DESKPET_DEV_MODE": "1"}
    )
    provider = ProviderInvocationCoordinator(uow, fault_script=fault_script)
    physical_calls = 0

    class Lease:
        def __init__(self, run_id: str) -> None:
            self.run_id = run_id
            self.revocation_epoch = 1

        async def release(self):
            return None

    class FaultingChildRecoveryDriver(FakeDriver):
        recovered_child_run_id: str | None = None
        invocation_id: str | None = None

        async def start(self, request):
            nonlocal physical_calls
            assert request.run_context is not None
            assert request.run_context.parent_run_id == parent.run_id

            async def invoke():
                nonlocal physical_calls
                physical_calls += 1
                return {"content": "must not run"}

            prepared = provider.prepare_attempt(
                {
                    "run_id": request.run_id,
                    "provider_id": "kimi",
                    "model_id": "kimi-k3",
                    "adapter_id": "openai-compatible",
                    "idempotency_group_id": "child-turn:provider-0",
                    "attempt_ordinal": 0,
                    "provider_chain_slot": 0,
                    "retry_ordinal": 0,
                    "fallback_ordinal": 0,
                    "stream_epoch": "1",
                    "request_payload": {
                        "messages": [{"role": "user", "content": "child"}]
                    },
                    "policy_snapshot": {
                        "purpose": "agent_response",
                        "session_id": request.session_id,
                        "root_run_id": request.run_context.root_run_id,
                        "parent_run_id": request.run_context.parent_run_id,
                        "profile_key": request.profile_key,
                        "sdk_retries": 0,
                    },
                    "invoke": invoke,
                }
            )
            self.invocation_id = prepared.identity.invocation_id
            claimed = await provider.claim_prepared(
                prepared, Lease(request.run_id)
            )
            try:
                await provider.start_and_ack(claimed, 1.0)
            except ProviderFaultInjectedError as exc:
                yield DriverTerminalCandidate(
                    request.run_id,
                    "failed",
                    error=str(exc),
                    correlation={"injection_ref": exc.injection_ref},
                )
                return
            raise AssertionError("child provider fault was not injected")

        async def signal(self, signal, recovery_lease=None):
            self.signals += 1
            assert signal.signal_id is not None
            saved = await uow.load_continuation(signal.run_id)
            assert saved is not None
            payload = dict(saved.payload)
            child_signals = list(payload.get("child_signals") or ())
            child_signals.append(
                {
                    "signal_id": signal.signal_id,
                    "kind": signal.kind,
                    "child_run_id": signal.child_run_id,
                }
            )
            payload["child_signals"] = child_signals
            _record, _saved, stored_event = await uow.ack_child_signal(
                signal.signal_id,
                expected_continuation_version=saved.version,
                continuation_payload=payload,
                event=RunEventCandidate(
                    event_key=f"child-signal:{signal.signal_id}",
                    kind=signal.kind,
                    status=(
                        OutcomeStatus.ACCEPTED
                        if signal.kind == "child_accepted"
                        else OutcomeStatus.FAILED
                    ),
                    driver_kind="react",
                    correlation={
                        "child_run_id": signal.child_run_id,
                        "signal_id": signal.signal_id,
                    },
                ),
                recovery_lease=recovery_lease,
            )
            yield PersistedEventCandidate(stored_event)
            if signal.kind == "child_accepted":
                return
            assert signal.kind == "child_terminal"
            assert signal.status == "failed"
            self.recovered_child_run_id = signal.child_run_id
            yield DriverTerminalCandidate(
                signal.run_id,
                "completed",
                "parent recovered after child provider failure",
                correlation={
                    "recovered_child_run_id": signal.child_run_id,
                    "recovery_reason": "child_provider_failure",
                },
            )

    driver = FaultingChildRecoveryDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver(
            "react", driver, durable_from_start=True,
        ),)),
    )
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, DelegateRun(
        run_id=parent.run_id, command_id="faulting-child",
        child_request={"task": "provider failure", "driver_kind": "react"},
        route_hint="react.default", capability_subset=(),
        attachment_policy=AttachmentPolicy.ATTACHED,
        join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
    ))
    reconciler = ChildReconciler(coordinator, kernel, owner="provider-fault-test")
    await reconciler.reconcile_commands_once(lease_seconds=5)
    assert not reconciler.last_errors, reconciler.last_errors

    child_ref = RunRef(command.child_run_id, "s1")
    actor = parent_context.actor()
    for _ in range(100):
        child = await uow.query(child_ref, actor)
        if child.status is RunStatus.FAILED:
            break
        await asyncio.sleep(0.01)
    assert child.status is RunStatus.FAILED
    assert driver.invocation_id is not None
    invocation = await uow.read_provider_invocation(
        command.child_run_id, driver.invocation_id
    )
    assert invocation is not None and invocation.status.value == "failed"
    assert physical_calls == 0

    registration = kernel._drivers[parent.spec.driver_kind]
    pending = await uow.list_pending_child_signals(parent.run_id, limit=10)
    assert [item.kind for item in pending] == ["accepted", "terminal"]
    for record in pending:
        signal = (
            ChildAcceptedSignal(
                record.parent_run_id,
                record.command_id,
                record.child_run_id,
                record.signal_id,
            )
            if record.kind == "accepted"
            else ChildTerminalSignal(
                record.parent_run_id,
                record.command_id,
                record.child_run_id,
                str(record.payload["status"]),
                record.payload.get("value"),
                record.signal_id,
            )
        )
        await kernel._runtime.consume(
            registration,
            parent,
            driver.signal(signal),
        )
    current = await uow.query(RunRef(parent.run_id, "s1"), actor)
    assert current.status is RunStatus.COMPLETED
    assert driver.recovered_child_run_id == command.child_run_id
    root_final = (await uow.list_events(parent.run_id))[-1]
    assert root_final.candidate.correlation == {
        "recovered_child_run_id": command.child_run_id,
        "recovery_reason": "child_provider_failure",
    }
    assert await fault_script.active_injection_count() == 0
    assert await kernel._drain_active(1.0)
    await uow.close()


@pytest.mark.asyncio
async def test_composed_agent_loop_child_fault_binds_identity_and_parent_recovers(
    tmp_path,
) -> None:
    """Exercise the production child provider seam, not a driver-local stand-in."""

    from agent.agent_loop import AgentLoop
    from deskpet.harness.drivers.react_loop import AgentLoopCollaborator

    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    parent_context = RunContext(
        session_id="s1",
        root_run_id="parent-composed-provider-recovery",
        parent_run_id=None,
        request_id="request-parent-composed-provider-recovery",
        turn_id="turn-parent-composed-provider-recovery",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-parent-composed-provider-recovery",
        principal_id="p1",
        auth_epoch=0,
    )
    parent = (
        await uow.create(
            RunCreate(
                run_id=parent_context.root_run_id,
                idempotency_key="root:parent-composed-provider-recovery",
                context=parent_context,
                payload_fingerprint="a" * 64,
                capability_fingerprint=parent_context.capability_hash,
                driver_kind="react",
                profile_key="react.default",
                persistence_level=PersistenceLevel.DURABLE,
                status=RunStatus.RUNNING,
            )
        )
    ).record
    await uow.persist_react_boundary(parent.run_id, 0, {"child_signals": []})

    fault_path = tmp_path / "composed-provider-faults.json"
    fault_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "rule_id": "composed-child-provider-failure",
                        "injection_ref": "composed-child-provider-failure-ref",
                        "session_id": "s1",
                        "workload_class": "main",
                        "callsite_id": "agent.root_turn",
                        "purpose": "agent_response",
                        "occurrence": 1,
                        "action": "child_provider_failure",
                        "run_kind": "child",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fault_script = ProviderFaultScriptV1.from_file(
        fault_path, environ={"DESKPET_DEV_MODE": "1"}
    )
    provider_coordinator = ProviderInvocationCoordinator(
        uow, fault_script=fault_script
    )

    class Lease:
        def __init__(self, run_id: str) -> None:
            self.run_id = run_id
            self.revocation_epoch = 1

        async def release(self):
            return None

    async def acquire_fence(run_id: str):
        return Lease(run_id)

    class StreamingProvider:
        provider_id = "kimi"
        model = "kimi-k3"
        adapter_id = "openai-compatible"
        adapter_version = "v1"

        def __init__(self) -> None:
            self.physical_calls = 0

        async def chat_with_fallback_stream(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            self.physical_calls += 1
            yield {
                "type": "final",
                "content": "must not run",
                "tool_calls": [],
                "usage": {},
            }

        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            self.physical_calls += 1
            raise AssertionError("provider transport must not run")

    class EmptyTools:
        def schemas(self, enabled_toolsets=None):
            del enabled_toolsets
            return []

        def prepared_execution_policy(self, call):
            del call
            return False, False

    physical_provider = StreamingProvider()
    tools = EmptyTools()
    collaborator = AgentLoopCollaborator(
        lambda _request: AgentLoop(physical_provider, tools),
        call_factory=tools,
        provider_invocation_coordinator=provider_coordinator,
        provider_fence_acquirer=acquire_fence,
    )
    child_driver = ReActDriver(collaborator, uow, tools)

    class ComposedChildRecoveryDriver(FakeDriver):
        recovered_child_run_id: str | None = None

        def bind_live_index(self, live):
            child_driver.bind_live_index(live)

        async def start(self, request):
            self.starts += 1
            assert request.run_context is not None
            assert request.run_context.parent_run_id == parent.run_id
            async for candidate in child_driver.start(request):
                yield candidate

        async def signal(self, signal, recovery_lease=None):
            self.signals += 1
            assert signal.signal_id is not None
            saved = await uow.load_continuation(signal.run_id)
            assert saved is not None
            payload = dict(saved.payload)
            child_signals = list(payload.get("child_signals") or ())
            child_signals.append(
                {
                    "signal_id": signal.signal_id,
                    "kind": signal.kind,
                    "child_run_id": signal.child_run_id,
                }
            )
            payload["child_signals"] = child_signals
            _record, _saved, stored_event = await uow.ack_child_signal(
                signal.signal_id,
                expected_continuation_version=saved.version,
                continuation_payload=payload,
                event=RunEventCandidate(
                    event_key=f"child-signal:{signal.signal_id}",
                    kind=signal.kind,
                    status=(
                        OutcomeStatus.ACCEPTED
                        if signal.kind == "child_accepted"
                        else OutcomeStatus.FAILED
                    ),
                    driver_kind="react",
                    correlation={
                        "child_run_id": signal.child_run_id,
                        "signal_id": signal.signal_id,
                    },
                ),
                recovery_lease=recovery_lease,
            )
            yield PersistedEventCandidate(stored_event)
            if signal.kind == "child_accepted":
                return
            assert signal.kind == "child_terminal"
            assert signal.status == "failed"
            self.recovered_child_run_id = signal.child_run_id
            yield DriverTerminalCandidate(
                signal.run_id,
                "completed",
                "parent recovered after composed child provider failure",
                correlation={
                    "recovered_child_run_id": signal.child_run_id,
                    "recovery_reason": "child_provider_failure",
                },
            )

    driver = ComposedChildRecoveryDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )
    child_runs = ChildRunCoordinator(uow)
    command = await child_runs.submit(
        parent,
        DelegateRun(
            run_id=parent.run_id,
            command_id="composed-faulting-child",
            child_request={"task": "provider failure", "driver_kind": "react"},
            route_hint="react.default",
            capability_subset=(),
            attachment_policy=AttachmentPolicy.ATTACHED,
            join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
        ),
    )
    reconciler = ChildReconciler(
        child_runs, kernel, owner="composed-provider-fault-test"
    )
    await reconciler.reconcile_commands_once(lease_seconds=5)
    assert not reconciler.last_errors, reconciler.last_errors

    child_ref = RunRef(command.child_run_id, "s1")
    actor = parent_context.actor()
    for _ in range(100):
        child = await uow.query(child_ref, actor)
        if child.status is RunStatus.FAILED:
            break
        await asyncio.sleep(0.01)
    assert child.status is RunStatus.FAILED
    assert physical_provider.physical_calls == 0

    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT invocation_id,policy_snapshot_json,status "
                "FROM execution_provider_invocations WHERE run_id=?",
                (command.child_run_id,),
            )
        ).fetchone()
    assert row is not None
    invocation_id, policy_json, invocation_status = row
    policy = json.loads(policy_json)
    assert invocation_status == "failed"
    assert policy == {
        "coordinated": True,
        "parent_run_id": parent.run_id,
        "profile_key": "react.default",
        "purpose": "agent_response",
        "root_run_id": parent.run_id,
        "sdk_retries": 0,
        "session_id": "s1",
        "stream": True,
    }
    invocation = await uow.read_provider_invocation(
        command.child_run_id, str(invocation_id)
    )
    assert invocation is not None and invocation.status.value == "failed"

    registration = kernel._drivers[parent.spec.driver_kind]
    pending = await uow.list_pending_child_signals(parent.run_id, limit=10)
    assert [item.kind for item in pending] == ["accepted", "terminal"]
    for record in pending:
        signal = (
            ChildAcceptedSignal(
                record.parent_run_id,
                record.command_id,
                record.child_run_id,
                record.signal_id,
            )
            if record.kind == "accepted"
            else ChildTerminalSignal(
                record.parent_run_id,
                record.command_id,
                record.child_run_id,
                str(record.payload["status"]),
                record.payload.get("value"),
                record.signal_id,
            )
        )
        await kernel._runtime.consume(
            registration,
            parent,
            driver.signal(signal),
        )

    current = await uow.query(RunRef(parent.run_id, "s1"), actor)
    assert current.status is RunStatus.COMPLETED
    assert driver.recovered_child_run_id == command.child_run_id
    root_final = (await uow.list_events(parent.run_id))[-1]
    assert root_final.candidate.correlation == {
        "recovered_child_run_id": command.child_run_id,
        "recovery_reason": "child_provider_failure",
    }
    assert await fault_script.active_injection_count() == 0
    assert await kernel._drain_active(1.0)
    await uow.close()


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
    await uow.persist_react_boundary(
        command.child_run_id, 0, {"checkpoint": "already-started"}
    )

    driver = FakeDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
    )
    await kernel._accept_precreated_child(scheduled)
    await _live_run(kernel, command.child_run_id).task

    assert driver.starts == 0
    assert driver.recovers == 1


@pytest.mark.asyncio
async def test_atomic_precreated_child_consumes_acceptance_and_starts_once(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    context = RunContext(
        session_id="s1", root_run_id="atomic-parent", parent_run_id=None,
        request_id="atomic-request", turn_id="atomic-turn", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="atomic-trace", principal_id="p1", auth_epoch=0,
    )
    parent = (await uow.create(RunCreate(
        run_id="atomic-parent", idempotency_key="root:atomic-parent", context=context,
        payload_fingerprint="a" * 64, capability_fingerprint="c" * 64,
        driver_kind="workflow", profile_key="workflow.default",
        persistence_level=PersistenceLevel.DURABLE, status=RunStatus.RUNNING,
    ))).record
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, DelegateRun(
        run_id=parent.run_id, command_id="atomic-child",
        child_request={"task": "atomic", "driver_kind": "workflow"},
        route_hint="workflow.default", capability_subset=(),
        attachment_policy=AttachmentPolicy.DETACHED, join_policy=JoinPolicy.DETACHED,
    ))
    leased = (await uow.lease_child_commands(owner="setup", limit=1, lease_seconds=30))[0]
    scheduled = await uow.schedule_child_command(
        command.operation_id, lease_owner="setup", lease_epoch=leased.schedule_lease_epoch,
    )
    continue_stream = asyncio.Event()

    class AtomicChildDriver(FakeDriver):
        async def start(self, request):
            self.starts += 1
            assert request.run_spec is not None
            capability_snapshot = {
                "tools": list(request.capability_snapshot.get("capabilities", ()))
            }
            await uow.start_workflow(
                request.run_spec,
                WorkflowRunSeed(
                    request_key=request.run_spec.idempotency_key,
                    workflow_name="fixture",
                    workflow_version="v1",
                    manifest_hash="m" * 64,
                    implementation_hash="i" * 64,
                    capability_hash=request.run_spec.capability_fingerprint,
                    capability_snapshot=capability_snapshot,
                    state_schema_version=1,
                    trace_id=request.run_spec.context.trace_id,
                    thread_id=request.run_id,
                ),
                accepted_event=RunEventCandidate(
                    event_key="atomic-accepted", kind="workflow.accepted",
                    status=OutcomeStatus.ACCEPTED, driver_kind="workflow",
                ),
            )
            accepted = (await uow.list_events(request.run_id))[-1]
            yield PersistedEventCandidate(accepted)
            await continue_stream.wait()

    driver = AtomicChildDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("workflow.default"),
            _profiles("workflow.default", "workflow"),
        ),
        drivers=driver_catalog((RegisteredDriver(
            "workflow", driver, durable_from_start=True, atomic_start=True,
        ),)),
    )
    await asyncio.gather(
        kernel._accept_precreated_child(scheduled),
        kernel._accept_precreated_child(scheduled),
    )
    active = _live_run(kernel, command.child_run_id)
    assert driver.starts == 1
    assert [event.kind for event in active.events] == ["workflow.accepted"]
    continue_stream.set()
    await active.task


def test_launcher_forwarding_facades_are_deleted() -> None:
    sources = "\n".join(
        (Path(__file__).parents[3] / path).read_text(encoding="utf-8")
        for path in (
            "backend/deskpet/harness/kernel.py",
            "backend/deskpet/harness/child_runs.py",
            "backend/deskpet/harness/reconciler.py",
        )
    )
    assert "KernelChildLauncher" not in sources
    assert "class ChildLauncher" not in sources


def test_runtime_type_hints_and_trusted_actor_mapping_are_resolvable() -> None:
    assert get_type_hints(DriverRuntime.__init__)["query"]
    context = RunContext(
        session_id="session", root_run_id="root", parent_run_id="parent",
        request_id="request", turn_id="turn", venue="text", workspace={},
        capability_hash="c" * 64, provider_plan={}, trace_id="trace",
        principal_id="principal", auth_epoch=7,
    )
    actor = context.actor()
    assert (
        actor.principal_id, actor.session_id, actor.auth_epoch, actor.root_run_id,
        actor.internal, actor.capability_hash, actor.expires_at,
    ) == ("principal", "session", 7, "root", False, None, None)


@pytest.mark.asyncio
async def test_recovery_failure_isolated_per_run() -> None:
    actor = ActorContext("principal", "session", 0, root_run_id="root")
    records = (
        SimpleNamespace(
            run_id="busy-run", context=SimpleNamespace(actor=lambda: actor)
        ),
        SimpleNamespace(
            run_id="healthy-run", context=SimpleNamespace(actor=lambda: actor)
        ),
    )

    class Uow:
        async def list_recoverable(self, **kwargs):
            return records

    class Kernel:
        async def recover(self, ref, supplied_actor):
            assert supplied_actor is actor
            if ref.run_id == "busy-run":
                raise StaleRecoveryLease("held by another owner")
            return "healthy-handle"

    reconciler = HarnessReconciler(Uow(), Kernel())

    handles = await reconciler.recover_pending()

    assert handles == ("healthy-handle",)
    assert reconciler.last_errors == (
        "busy-run:StaleRecoveryLease:held by another owner",
    )


@pytest.mark.asyncio
async def test_recovery_skips_run_already_owned_by_cancel_intent() -> None:
    actor = ActorContext("principal", "session", 0, root_run_id="root")
    record = SimpleNamespace(
        run_id="cancel-owned",
        context=SimpleNamespace(actor=lambda: actor),
    )

    class Uow:
        async def list_recoverable(self, **kwargs):
            return (record,)

    class Kernel:
        async def recover(self, ref, supplied_actor):
            assert ref.run_id == "cancel-owned"
            assert supplied_actor is actor
            raise IdempotencyConflict(
                "run_intent_conflict",
                "another cancel intent already owns the run",
            )

    reconciler = HarnessReconciler(Uow(), Kernel())

    assert await reconciler.recover_pending() == ()
    assert reconciler.last_errors == ()
    assert reconciler.last_fatal_recovery_errors == ()


@pytest.mark.asyncio
async def test_cancel_requested_parent_acks_child_signals_without_resuming_driver() -> None:
    parent = SimpleNamespace(run_id="parent", status=RunStatus.CANCEL_REQUESTED)
    signal = SimpleNamespace(
        signal_id="signal",
        parent_run_id="parent",
        command_id="command",
        child_run_id="child",
        kind="terminal",
        payload={"status": "cancelled"},
        delivered_at=None,
    )

    class Uow:
        acknowledgements = 0

        async def list_pending_child_signal_parents(self, **kwargs):
            return (parent,)

        async def list_pending_child_signals(self, *args, **kwargs):
            return (signal,)

        async def ack_child_signal(self, signal_id):
            assert signal_id == "signal"
            self.acknowledgements += 1

    class Kernel:
        async def _deliver_child_signal(self, *args, **kwargs):
            raise AssertionError("cancel_requested parents must not resume their driver")

    uow = Uow()
    reconciler = HarnessReconciler(uow, Kernel(), coordinator=object())

    await reconciler.reconcile_signals_once()

    assert reconciler.last_errors == ()
    assert uow.acknowledgements == 1


@pytest.mark.asyncio
async def test_borrowed_signal_failure_and_release_failure_are_both_recorded() -> None:
    parent = SimpleNamespace(run_id="parent", status=RunStatus.RUNNING)
    signal = SimpleNamespace(
        signal_id="signal", parent_run_id="parent", command_id="command",
        child_run_id="child", kind="accepted", payload={}, delivered_at=None,
    )

    class Uow:
        releases = 0
        acknowledgements = 0

        async def list_pending_child_signal_parents(self, **kwargs):
            return (parent,)

        async def list_pending_child_signals(self, *args, **kwargs):
            return (signal,)

        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if isinstance(subject, str):
                return RecoveryLease(subject, owner, 1, 9999)
            if lease_seconds is None:
                self.releases += 1
                raise RuntimeError("release failed")
            return subject

        async def ack_child_signal(self, *args, **kwargs):
            self.acknowledgements += 1

    async def deliver(*args):
        raise ValueError("signal failed")

    uow = Uow()
    reconciler = HarnessReconciler(
        uow, SimpleNamespace(_deliver_child_signal=deliver),
        coordinator=object(),
    )
    await reconciler.reconcile_signals_once()

    assert reconciler.last_errors == (
        "signal:ValueError:signal failed",
        "parent:release:RuntimeError:release failed",
    )
    assert uow.releases == 1 and uow.acknowledgements == 0
    assert signal.delivered_at is None


@pytest.mark.asyncio
async def test_borrowed_signal_cancel_closes_stream_and_release_cannot_mask_cancel() -> None:
    parent = SimpleNamespace(run_id="parent", status=RunStatus.RUNNING)
    signal = SimpleNamespace(
        signal_id="signal", parent_run_id="parent", command_id="command",
        child_run_id="child", kind="accepted", payload={}, delivered_at=None,
    )

    class Uow:
        releases = 0
        acknowledgements = 0

        async def list_pending_child_signal_parents(self, **kwargs):
            return (parent,)

        async def list_pending_child_signals(self, *args, **kwargs):
            return (signal,)

        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if isinstance(subject, str):
                return RecoveryLease(subject, owner, 1, 9999)
            if lease_seconds is None:
                self.releases += 1
                raise RuntimeError("release failed")
            return subject

        async def ack_child_signal(self, *args, **kwargs):
            self.acknowledgements += 1

    started, closed = asyncio.Event(), asyncio.Event()

    async def stream(_lease):
        try:
            started.set()
            await asyncio.Event().wait()
            yield TokenCandidate("parent", "unreachable")
        finally:
            closed.set()

    uow = Uow()
    runtime = DriverRuntime(
        uow=uow, live=BoundedLiveIndex(max_runs=1),
        query=lambda *args: None, finalize=lambda *args, **kwargs: None,
    )
    registration = RegisteredDriver("react", FakeDriver())

    async def deliver(_parent, _signal, lease):
        await runtime.consume_fenced(
            registration, parent, stream, lease, heartbeat_interval=1.0,
        )

    reconciler = HarnessReconciler(
        uow, SimpleNamespace(_deliver_child_signal=deliver),
        coordinator=object(),
    )
    task = asyncio.create_task(reconciler.reconcile_signals_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed.is_set() and uow.releases == 1
    assert uow.acknowledgements == 0 and signal.delivered_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("renew_failure", [None, RuntimeError, StaleRecoveryLease])
async def test_slow_child_signal_renews_parent_lease_before_fenced_write(
    tmp_path, renew_failure
) -> None:
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    class TrackingUow(SqliteExecutionUnitOfWork):
        renewals = 0

        async def recovery_scope(self, subject, *, owner=None, lease_seconds=30.0):
            if not isinstance(subject, str) and lease_seconds is not None:
                self.renewals += 1
                if renew_failure is not None and self.renewals == 3:
                    raise renew_failure("injected heartbeat failure")
            return await super().recovery_scope(
                subject, owner=owner, lease_seconds=lease_seconds
            )

    class SlowSignalDriver(FakeDriver):
        closed = False

        async def signal(self, signal, recovery_lease=None):
            try:
                for _ in range(4):
                    prior = uow.renewals
                    clock.advance(8)
                    for _ in range(100):
                        if uow.renewals > prior:
                            break
                        await asyncio.sleep(0.005)
                    assert uow.renewals > prior
                await uow.persist_react_boundary(
                    signal.run_id, 0, {"slow_signal_applied": True},
                    recovery_lease=recovery_lease,
                )
                if False:
                    yield TokenCandidate(signal.run_id, "")
            finally:
                self.closed = True

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
    driver = SlowSignalDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
        child_signal_heartbeat_interval=0.005,
    )

    scheduler = ChildReconciler(
        coordinator, kernel, owner="slow-scheduler"
    )
    await scheduler.reconcile_signals_once()
    active = kernel._live.get(parent.run_id)
    assert active is not None and active.task is not None
    await asyncio.gather(active.task, return_exceptions=True)
    if renew_failure is not None:
        # The Run-owned task preserves the failure without blocking the
        # Supervisor. Its next bounded pass observes and reports that result.
        await scheduler.reconcile_signals_once()

    continuation = await uow.load_continuation(parent.run_id)
    if renew_failure is None:
        assert scheduler.last_errors == ()
        assert continuation is not None
        assert dict(continuation.payload) == {"slow_signal_applied": True}
        assert uow.renewals >= 4 and clock.now > 1030
    else:
        assert "injected heartbeat failure" in scheduler.last_errors[0]
        assert continuation is None and driver.closed
        assert (await uow.list_pending_child_signals(parent.run_id))[0].delivered_at is None
    assert await scheduler.drain(1.0)
    await uow.close()


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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
        child_runs=ChildRunCoordinator(uow),
    )
    handle = await value.start(RunRequest("delegate", "req-tree", "turn-1"), host())
    operation_id = stable_child_operation_id(handle.ref.run_id, "delegate-1")
    for _ in range(100):
        if await uow.get_child_command(operation_id) is not None:
            break
        await asyncio.sleep(0.01)
    await ChildReconciler(
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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
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
    terminal = [event for event in events if event.kind == "run.final"]
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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
        tool_executor=EffectBatchExecutor(uow, PreparedRegistry()),
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
@pytest.mark.parametrize("recovery_before_close", [False, True])
async def test_runtime_keeps_mixed_batch_pending_when_one_physical_call_is_late(
    tmp_path, recovery_before_close: bool,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    registry, collaborator = MixedLateRegistry(), MixedLateCollaborator()
    driver = ReActDriver(collaborator, uow, registry)
    executor = EffectBatchExecutor(uow, registry)
    value = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            StaticClassifier("react.default"),
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
        tool_executor=executor,
    )
    reconciler = HarnessReconciler(
        uow, value, executor, item_timeout=0.05
    )
    executor._bind_ready_callback(lambda _run_id: reconciler.trigger())
    handle = await value.start(RunRequest("mixed", "req-mixed", "turn-1"), host())
    for _ in range(300):
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
        if "effect-1" not in registry.outcomes:
            break
        await asyncio.sleep(0.01)
    assert (await registry.observe_late_prepared("effect-1"))[0] == "pending"
    # Settling the first item is durable before the Run-owned task finishes
    # unwinding.  Synchronize on that ownership boundary instead of racing a
    # manual recovery against a still-live task under a loaded test process.
    active = value._live.get(handle.ref.run_id)
    assert active is not None and active.task is not None
    await asyncio.wait_for(asyncio.shield(active.task), timeout=3.0)
    assert not active.task.cancelled() and active.task.exception() is None
    registry.block_late_receipt = True
    await reconciler.drain(1.0)
    if recovery_before_close:
        registry.complete_late()
        await reconciler.recover_pending(
            only_run_ids=frozenset({handle.ref.run_id})
        )
        await asyncio.wait_for(registry.late_receipt_started.wait(), timeout=3.0)
    else:
        asyncio.get_running_loop().call_later(0.01, registry.complete_late)
    close_task = asyncio.create_task(HarnessRuntime(
        value, None, None, None, reconciler,
        (RegisteredDriver("react", driver),), executor,
    ).close(timeout=2.0))
    await asyncio.wait_for(registry.late_receipt_started.wait(), timeout=3.0)
    assert registry.ready_late_prepared_run_ids() == frozenset(
        {handle.ref.run_id}
    )
    assert close_task.done() is False
    assert collaborator.closed is False
    asyncio.get_running_loop().call_later(0.1, registry.release_late_receipt.set)
    await close_task
    assert collaborator.closed is True
    assert registry.ready_late_prepared_run_ids() == frozenset()
    async with aiosqlite.connect(tmp_path / "workflow.db") as db:
        late = await (
            await db.execute(
                "SELECT status,receipt_ref FROM execution_effects WHERE effect_id='effect-2'"
            )
        ).fetchone()
        late_public = await (
            await db.execute(
                "SELECT status,projection_json FROM execution_tool_public_projections "
                "WHERE effect_id='effect-2'"
            )
        ).fetchone()
    assert late == ("succeeded", "receipt-late")
    assert late_public is not None
    assert late_public[0] == "succeeded"
    assert json.loads(late_public[1])["status"] == "succeeded"
    for _ in range(100):
        if collaborator.resumes:
            break
        await asyncio.sleep(0.01)
    assert len(collaborator.resumes) == 1
    recovered_boundary = collaborator.resumes[0][0]
    assert [item.value["written"] for item in recovered_boundary.outcomes] == [1, 2]
    assert value._live.get(handle.ref.run_id) is None


@pytest.mark.asyncio
async def test_restart_after_close_bound_keeps_missing_late_evidence_unknown(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    registry, first_collaborator = MixedLateRegistry(), MixedLateCollaborator()
    first_driver = ReActDriver(first_collaborator, uow, registry)
    first_executor = EffectBatchExecutor(uow, registry)
    first = RunKernel(
        uow=uow,
        router=RegisteredRouter(StaticClassifier("react.default"),
                                _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver("react", first_driver),)),
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
    first_recovery = HarnessReconciler(uow, first, first_executor)
    await HarnessRuntime(
        first, None, None, None, first_recovery,
        (RegisteredDriver("react", first_driver),), first_executor,
    ).close(timeout=0.01)
    close_bound = await uow.query(
        handle.ref,
        host().actor(root_run_id=handle.root_run_id),
    )
    assert close_bound.status not in TERMINAL_RUN_STATUSES

    restarted_uow = SqliteExecutionUnitOfWork(path)
    await restarted_uow.initialize()
    restarted_collaborator = MixedLateCollaborator()
    missing = PreparedRegistry()
    restarted = RunKernel(
        uow=restarted_uow,
        router=RegisteredRouter(StaticClassifier("react.default"),
                                _profiles("react.default", "react")),
        drivers=driver_catalog((RegisteredDriver(
            "react", ReActDriver(restarted_collaborator, restarted_uow, missing)
        ),)),
        tool_executor=EffectBatchExecutor(restarted_uow, missing),
    )
    await HarnessReconciler(restarted_uow, restarted).recover_pending()
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
        if _live_run(restarted, handle.ref.run_id).task.done():
            break
        await asyncio.sleep(0.01)
    assert _live_run(restarted, handle.ref.run_id).task.done()
    await asyncio.sleep(0.05)  # let aiosqlite worker threads publish their final close


@pytest.mark.asyncio
async def test_cancelled_terminal_run_quarantines_late_effect_without_driver_resume(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    run_id = "cancelled-late-run"
    context = RunContext(
        session_id="s1", root_run_id=run_id, parent_run_id=None,
        request_id="request-cancelled-late", turn_id="turn-cancelled-late",
        venue="text", workspace={}, capability_hash="c" * 64,
        provider_plan={}, trace_id="trace-cancelled-late",
        principal_id="principal-s1", auth_epoch=1,
    )
    record = (await uow.create(RunCreate(
        run_id=run_id,
        idempotency_key="root:s1:request-cancelled-late:turn-cancelled-late",
        context=context,
        payload_fingerprint=fingerprint_json({"late": True}),
        capability_fingerprint=context.capability_hash,
        driver_kind="react", profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    ))).record
    call = PreparedToolCall.prepare(
        tool_name="read-file", stable_call_id="call-late",
        final_params={"path": "README.md"}, tool_spec_version="v1",
        schema_hash="schema", permission_policy_version="v1",
        effect_type="idempotent_read", effect_policy_version="v1",
    )
    tool_context = ToolExecutionContext(
        scope_id="scope", session_id="s1", request_id=context.request_id,
        root_run_id=run_id, run_id=run_id, call_id=call.stable_call_id,
        effect_id="effect-late", capability_hash=context.capability_hash,
        scope_hash="scope",
    )
    claim = await uow.claim_tool_call(
        None, host().actor(root_run_id=run_id), run_id=run_id,
        expected_session_id="s1", call_id=call.stable_call_id,
        effect_id=tool_context.effect_id, tool_name=call.tool_name,
        args_hash=call.args_hash, capability_hash=tool_context.capability_hash,
        scope_hash=tool_context.scope_hash, effect_type=call.effect_type,
        policy={"kind": call.effect_type, "version": call.effect_policy_version},
        prepared=call.to_dict(), worker_owner="kernel", worker_epoch=1,
    )
    started = await uow.mark_effect_dispatch_started(
        tool_context.effect_id, claim.attempt_no, claim.effect_version,
        "tool-started", fingerprint_json({"started": True}),
    )
    unknown = await uow.mark_effect_inflight_may_complete(
        tool_context.effect_id, claim.attempt_no, started.effect_version,
        "consumer-cancelled", fingerprint_json({"cancelled": True}),
    )
    latest = await uow.query(
        RunRef(run_id, "s1"), host().actor(root_run_id=run_id)
    )
    await uow.commit_run_outcome(
        run_id,
        expected_version=latest.version,
        terminal_status=RunStatus.CANCELLED,
        event=RunEventCandidate(
            event_key="run:cancelled-late:final", kind="run.final",
            status=OutcomeStatus.CANCELLED, driver_kind="react",
            payload={"reason": "user_stop"},
        ),
    )

    class LateRegistry:
        ready = True
        acknowledgements = 0

        def _set_prepared_ready_callback(self, _callback):
            return None

        def ready_late_prepared_run_ids(self):
            return frozenset({run_id}) if self.ready else frozenset()

        def ready_late_prepared_effect_ids(self, candidate_run_id):
            assert candidate_run_id == run_id
            return (tool_context.effect_id,) if self.ready else ()

        async def observe_late_prepared(self, effect_id):
            assert effect_id == tool_context.effect_id
            return "complete", NormalizedToolOutcome.success(
                {"text": "must never resume the cancelled driver"}
            )

        def acknowledge_prepared_effect(self, effect_id):
            assert effect_id == tool_context.effect_id
            self.acknowledgements += 1
            self.ready = False

        async def close_prepared_executions(self, _timeout):
            return None

    class NoResumeKernel:
        _live = BoundedLiveIndex(max_runs=4)
        recover_calls = 0

        async def recover(self, *_args, **_kwargs):
            self.recover_calls += 1
            raise AssertionError("terminal late outcome must not resume the driver")

    registry = LateRegistry()
    executor = EffectBatchExecutor(uow, registry)  # type: ignore[arg-type]
    kernel = NoResumeKernel()
    reconciler = HarnessReconciler(uow, kernel, executor)

    await reconciler.reconcile_all()
    await reconciler.reconcile_all()

    handoff = await uow.read_effect_handoff(tool_context.effect_id)
    assert unknown.status == "unknown"
    assert handoff is not None
    assert (
        handoff.status,
        handoff.handoff_state,
        handoff.completion_disposition,
    ) == (
        "late_reconciled",
        "reconciled",
        "reconciled_completed_suppressed",
    )
    assert registry.acknowledgements == 1
    assert kernel.recover_calls == 0
    terminal = await uow.query(
        RunRef(run_id, "s1"), host().actor(root_run_id=run_id)
    )
    assert terminal.status is RunStatus.CANCELLED
    await uow.close()


@pytest.mark.asyncio
async def test_real_latch_cancel_release_reconciles_without_driver_resume(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    run_id = "real-latch-cancelled-run"
    run_context = RunContext(
        session_id="s1", root_run_id=run_id, parent_run_id=None,
        request_id="request-real-latch", turn_id="turn-real-latch",
        venue="text", workspace={}, capability_hash="c" * 64,
        provider_plan={}, trace_id="trace-real-latch",
        principal_id="principal-s1", auth_epoch=1,
    )
    record = (await uow.create(RunCreate(
        run_id=run_id,
        idempotency_key="root:s1:request-real-latch:turn-real-latch",
        context=run_context,
        payload_fingerprint=fingerprint_json({"real_latch": True}),
        capability_fingerprint=run_context.capability_hash,
        driver_kind="react", profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    ))).record
    script_path = tmp_path / "tool-latch.json"
    script_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "rule_id": "real-late-read",
                        "injection_ref": "real-late-read-ref",
                        "session_id": "s1",
                        "tool_name": "file_read",
                        "occurrence": 1,
                        "armed_file": "real-late-read.armed.json",
                        "release_file": "real-late-read.release",
                        "timeout_seconds": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    handler_calls = 0

    async def read_handler(_args, _context):
        nonlocal handler_calls
        handler_calls += 1
        return json.dumps({"ok": True, "text": "already produced"})

    registry.register(
        "file_read",
        "file",
        {
            "name": "file_read",
            "description": "test read",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda _args, _task_id: "{}",
        context_handler=read_handler,
        permission_category="read_file",
        concurrency_safe=True,
        outcome_parser_id="json_error_envelope_v1",
    )
    registry.set_tool_completion_latch(
        ToolCompletionLatchScriptV1.from_file(
            script_path, environ={"DESKPET_DEV_MODE": "1"}
        )
    )
    call = registry.prepare_call(
        "file_read", {"path": "README.md"}, "s1", "call-real-latch"
    )
    tool_context = ToolExecutionContext(
        scope_id="scope", session_id="s1", request_id=run_context.request_id,
        root_run_id=run_id, run_id=run_id, call_id=call.stable_call_id,
        effect_id="effect-real-latch",
        turn_id=run_context.turn_id, trace_id=run_context.trace_id,
        capability_hash=run_context.capability_hash, scope_hash="scope",
    )
    executor = EffectBatchExecutor(uow, registry)
    execution = asyncio.create_task(
        executor.execute(
            record,
            run_context.actor(),
            ExecuteTools(
                run_id,
                "command-real-latch",
                (call,),
                (tool_context,),
                (0,),
                effectful=(False,),
            ),
        )
    )
    armed = tmp_path / "real-late-read.armed.json"
    started_handoff = None
    for _ in range(200):
        if armed.exists():
            started_handoff = await uow.read_effect_handoff(
                tool_context.effect_id
            )
            if (
                started_handoff is not None
                and started_handoff.handoff_state == "started"
            ):
                break
        await asyncio.sleep(0.005)
    assert armed.exists() and handler_calls == 1
    assert not execution.done(), execution.result()
    armed_payload = json.loads(armed.read_text(encoding="utf-8"))
    assert (
        armed_payload["session_id"],
        armed_payload["run_id"],
        armed_payload["effect_id"],
        armed_payload["tool_name"],
    ) == ("s1", run_id, tool_context.effect_id, "file_read")
    assert started_handoff is not None
    assert started_handoff.handoff_state == "started"

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    before = await uow.read_effect_handoff(tool_context.effect_id)
    assert before is not None
    assert (before.status, before.handoff_state) == (
        "unknown", "started_may_complete"
    )
    latest = await uow.query(RunRef(run_id, "s1"), run_context.actor())
    await uow.commit_run_outcome(
        run_id,
        expected_version=latest.version,
        terminal_status=RunStatus.CANCELLED,
        event=RunEventCandidate(
            event_key="run:real-latch:final", kind="run.final",
            status=OutcomeStatus.CANCELLED, driver_kind="react",
            payload={"reason": "user_stop"},
        ),
    )
    (tmp_path / "real-late-read.release").write_text(
        "release", encoding="utf-8"
    )
    await registry.close_prepared_executions(1)

    class NoResumeKernel:
        _live = BoundedLiveIndex(max_runs=4)
        recover_calls = 0

        async def recover(self, *_args, **_kwargs):
            self.recover_calls += 1
            raise AssertionError("cancelled Driver must not resume")

    kernel = NoResumeKernel()
    reconciler = HarnessReconciler(uow, kernel, executor)
    await reconciler.reconcile_all()
    await reconciler.reconcile_all()

    after = await uow.read_effect_handoff(tool_context.effect_id)
    assert after is not None
    assert (
        after.status,
        after.handoff_state,
        after.completion_disposition,
    ) == (
        "late_reconciled",
        "reconciled",
        "reconciled_completed_suppressed",
    )
    assert kernel.recover_calls == 0
    assert executor.ready_run_ids() == frozenset()
    terminal = await uow.query(RunRef(run_id, "s1"), run_context.actor())
    assert terminal.status is RunStatus.CANCELLED
    await uow.close()


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
    claim = await uow.claim_tool_call(
        None, host().actor(root_run_id=run_id), run_id=run_id,
        expected_session_id="s1", call_id=call.stable_call_id,
        effect_id=tool_context.effect_id, tool_name=call.tool_name,
        args_hash=call.args_hash, capability_hash=tool_context.capability_hash,
        scope_hash=tool_context.scope_hash, effect_type=call.effect_type,
        policy={"kind": call.effect_type, "version": call.effect_policy_version},
        prepared=call.to_dict(), worker_owner="kernel", worker_epoch=1,
    )
    await uow.persist_react_boundary(run_id, 0, {"pending": True})
    outcome = NormalizedToolOutcome.success({"reused": True})
    await uow.settle_effect(
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
        tool_executor=EffectBatchExecutor(uow, ReuseOnlyRegistry()),
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
async def test_ephemeral_read_tool_batch_does_not_require_durable_run() -> None:
    run_id = "ephemeral-read-run"
    context = RunContext(
        session_id="s1", root_run_id=run_id, parent_run_id=None,
        request_id="request-read", turn_id="turn-read", venue="text",
        workspace={}, capability_hash="c" * 64, provider_plan={},
        trace_id="trace-read", principal_id="principal-s1", auth_epoch=1,
    )
    record = RunKernel._ephemeral_record(RunCreate(
        run_id=run_id, idempotency_key="root:s1:request-read:turn-read",
        context=context, payload_fingerprint=fingerprint_json({"read": True}),
        capability_fingerprint=context.capability_hash, driver_kind="react",
        profile_key="react.default", persistence_level=PersistenceLevel.EPHEMERAL,
    ))
    call = PreparedToolCall.prepare(
        tool_name="file_read", stable_call_id="call-read",
        final_params={"path": "calculator.py"}, tool_spec_version="v1",
        schema_hash="schema", permission_policy_version="v1",
        effect_type="idempotent_read", effect_policy_version="v1",
    )
    tool_context = ToolExecutionContext(
        scope_id="scope", session_id="s1", request_id=context.request_id,
        root_run_id=run_id, run_id=run_id, call_id=call.stable_call_id,
        effect_id="effect-read", capability_hash=context.capability_hash,
        scope_hash="scope",
    )

    class ReadExecutor:
        async def execute(self, _record, _actor, command, **_kwargs):
            outcome = NormalizedToolOutcome.success({"text": "return left - right"})
            return EffectBatch(
                ToolOutcomesSignal(
                    command.run_id, command.command_id, (outcome,),
                    (OutcomeStatus.SUCCEEDED,), command.original_indexes, ({},),
                ),
                (),
            )

        async def acknowledge_committed(self, _ready_refs):
            return None

    async def unexpected_query(_ref, _actor):
        raise AssertionError("ephemeral read must not query a durable Run")

    driver = RecordingSignalDriver()
    runtime = DriverRuntime(
        uow=SimpleNamespace(), live=BoundedLiveIndex(max_runs=4),
        query=unexpected_query, finalize=lambda *args, **kwargs: None,
        tool_executor=ReadExecutor(),
    )
    await runtime.consume_candidate(
        RegisteredDriver("react", driver), record,
        ExecuteTools(
            run_id, "command-read", (call,), (tool_context,), (0,),
            effectful=(False,),
        ),
    )

    assert driver.last_signal.outcomes[0].value == {
        "text": "return left - right"
    }


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
    await uow.claim_tool_call(
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
        tool_executor=EffectBatchExecutor(uow, registry),
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


@pytest.mark.asyncio
async def test_all_late_batch_does_not_signal_driver(tmp_path) -> None:
    class AllLateRegistry(InterleavingRegistry):
        async def execute_prepared(self, call, **kwargs):
            self.physical_calls.append(call.stable_call_id)
            return NormalizedToolOutcome.malformed("still running")

        def take_prepared_execution_metadata(self, effect_id):
            return {"late_pending": True}

    registry, driver = AllLateRegistry(), RecordingSignalDriver()
    _, record, runtime, registration, command = await _single_effect_fixture(
        tmp_path, registry, driver
    )

    consumed = await runtime.consume_candidate(registration, record, command)

    assert consumed is False
    assert driver.last_signal is None
    assert registry.acknowledged == []


@pytest.mark.asyncio
async def test_ready_late_ready_batch_preserves_original_ready_order(tmp_path) -> None:
    registry, driver = InterleavingRegistry(), RecordingSignalDriver()
    uow, record, runtime, registration, _ = await _single_effect_fixture(
        tmp_path, registry, driver
    )
    calls = tuple(
        PreparedToolCall.prepare(
            tool_name=f"write-{index}", stable_call_id=f"call-{index}",
            final_params={"value": index}, tool_spec_version="v1",
            schema_hash="schema", permission_policy_version="v1",
            effect_type="opaque_manual", effect_policy_version="v1",
        )
        for index in range(3)
    )
    contexts = tuple(
        ToolExecutionContext(
            scope_id="scope", session_id="s1", request_id=record.context.request_id,
            root_run_id=record.run_id, run_id=record.run_id,
            call_id=call.stable_call_id, effect_id=f"effect-{index}",
            capability_hash=record.context.capability_hash, scope_hash="scope",
        )
        for index, call in enumerate(calls)
    )
    middle, middle_context = calls[1], contexts[1]
    await uow.claim_tool_call(
        None, host().actor(root_run_id=record.run_id), run_id=record.run_id,
        expected_session_id="s1", call_id=middle.stable_call_id,
        effect_id=middle_context.effect_id, tool_name=middle.tool_name,
        args_hash=middle.args_hash, capability_hash=middle_context.capability_hash,
        scope_hash=middle_context.scope_hash, effect_type=middle.effect_type,
        policy={"kind": middle.effect_type, "version": middle.effect_policy_version},
        prepared=middle.to_dict(), worker_owner="kernel", worker_epoch=1,
    )

    await runtime.consume_candidate(
        registration, record,
        ExecuteTools(
            record.run_id, "command-three", calls, contexts, (9, 2, 4),
            effectful=(True, True, True),
        ),
    )

    assert driver.last_signal.original_indexes == (9, 4)
    assert registry.physical_calls == ["call-0", "call-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["signal", "settle"])
async def test_signal_or_atomic_settle_failure_never_acknowledges_registry(
    tmp_path, failure: str,
) -> None:
    registry = InterleavingRegistry()

    class FailingSettlementDriver(RecordingSignalDriver):
        def __init__(self) -> None:
            super().__init__()
            self.uow = None

        async def signal(self, signal):
            self.last_signal = signal
            if failure == "signal":
                raise RuntimeError("signal failed before settlement")
            claim = dict(signal.metadata[0]["effect_claim"])
            await self.uow.settle_effect(
                "effect-ready",
                expected_effect_version=int(claim["effect_version"]) + 1,
                attempt_no=int(claim["attempt_no"]),
                worker_owner=str(claim["worker_owner"]),
                worker_epoch=int(claim["worker_epoch"]),
                status="succeeded", outcome=signal.outcomes[0].to_dict(),
            )
            if False:
                yield TokenCandidate(signal.run_id, "")

    driver = FailingSettlementDriver()
    uow, record, runtime, registration, command = await _single_effect_fixture(
        tmp_path, registry, driver
    )
    driver.uow = uow

    with pytest.raises(Exception):
        await runtime.consume_candidate(registration, record, command)

    assert registry.acknowledged == []


@pytest.mark.asyncio
async def test_registry_is_acknowledged_once_after_durable_settlement(tmp_path) -> None:
    registry = InterleavingRegistry()

    class SettlingDriver(RecordingSignalDriver):
        def __init__(self) -> None:
            super().__init__()
            self.uow = None

        async def signal(self, signal):
            self.last_signal = signal
            claim = dict(signal.metadata[0]["effect_claim"])
            await self.uow.settle_effect(
                "effect-ready", expected_effect_version=int(claim["effect_version"]),
                attempt_no=int(claim["attempt_no"]),
                worker_owner=str(claim["worker_owner"]),
                worker_epoch=int(claim["worker_epoch"]), status="succeeded",
                outcome=signal.outcomes[0].to_dict(),
            )
            if False:
                yield TokenCandidate(signal.run_id, "")

    driver = SettlingDriver()
    uow, record, runtime, registration, command = await _single_effect_fixture(
        tmp_path, registry, driver
    )
    driver.uow = uow

    await runtime.consume_candidate(registration, record, command)

    assert registry.acknowledged == ["effect-ready"]
    assert await uow.read_effect_outcome(
        run_id=record.run_id, call_id="call-effect", effect_id="effect-ready",
        args_hash=command.calls[0].args_hash,
        capability_hash=command.contexts[0].capability_hash,
        scope_hash=command.contexts[0].scope_hash,
    ) is not None


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
