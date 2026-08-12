from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType, SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from deskpet.agent.task_work_context import TaskWorkContextResolver
from deskpet.capabilities.store import (
    CAPABILITY_OPERATION_PHASES,
    CapabilityStore,
    initialize_capability_database,
)
from deskpet.capabilities.builder import (
    CapabilityBuildLaunch,
    CapabilityBuildLineage,
    CapabilityBuildSearchEvidence,
)
from deskpet.capabilities.contracts import CatalogStamp
from deskpet.capabilities.refresh import (
    CapabilityRefreshError,
    CapabilityRefreshStagingService,
)
from deskpet.capabilities.refresh_contracts import (
    CapabilityOperationReceipt,
    CapabilityRefreshIntent,
)
from deskpet.execution.contracts import (
    ActorContext,
    DecisionSignal as DurableDecisionSignal,
    ExternalWaitState,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    fingerprint_json,
    root_idempotency_key,
    thaw_json,
)
from deskpet.execution.evidence import EvidenceContext, UNKNOWN_EVIDENCE
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.permissions.task_grants import ResourceSelector
from deskpet.harness.child_runs import (
    ChildRunCoordinator,
)
from deskpet.harness.reconciler import HarnessReconciler as _HarnessReconciler
from deskpet.harness.drivers.react import (
    AgentLoopCollaborator,
    AgentLoopToolInterceptionError,
    ReActDriver,
    ReactControlBatch,
    ReactCommandBoundary,
    ReactEmission,
    ReactFailure,
    ReactFallback,
    ReactFinal,
    ReactToken,
    ReactToolBatch,
)
from deskpet.harness.drivers.react_artifact_completion import (
    remember_artifact_completion,
    replay_artifact_completions,
)
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.ports import (
    AttachmentPolicy,
    CancelAcknowledgedCandidate,
    ChildAcceptedCandidate,
    ChildAcceptedSignal,
    ChildTerminalSignal,
    ContextCompactedCandidate,
    DecisionSignal,
    DelegateRun,
    DriverStart,
    DriverTerminalCandidate,
    ExecuteTools,
    JoinPolicy,
    OpenDecision,
    ProviderFallbackCandidate,
    TokenCandidate,
    ToolOutcomesSignal as CanonicalToolOutcomesSignal,
    UserContinuationSignal,
)
from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolExposureIntent,
)
from deskpet.tools.prepared_snapshot import dump_context_os_snapshot
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.orchestration_controls import (
    EXTERNAL_ACTION_WAIT,
    PROJECT_DIRECTORY_SELECT,
)
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedTarget,
    PreparedToolCall,
    TargetMode,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork, StaleRecoveryLease


CAPABILITY_HASH = fingerprint_json({"tools": ["read", "write"]})
SCOPE_HASH = fingerprint_json({"workspace": "F:/workspace"})


def _actor(run_id: str = "run-react") -> ActorContext:
    return ActorContext("principal-react", "session-react", 0, run_id)


def _bind_live(driver, *run_ids: str):
    live = BoundedLiveIndex()
    for run_id in run_ids or ("run-react",):
        live.add(run_id, _actor(run_id))
    driver.bind_live_index(live)
    return driver, live


def _factory(loop):
    return lambda _request: loop


def _react_driver(
    collaborator,
    store,
    *run_ids: str,
    auto_mode_check=None,
    authorization_runtime=None,
):
    return _bind_live(
        ReActDriver(
            collaborator,
            store,
            PolicyRegistry(),
            auto_mode_check=auto_mode_check,
            authorization_runtime=authorization_runtime,
        ),
        *run_ids,
    )[0]


def _durable_signal(decision, response: Mapping[str, Any]) -> DurableDecisionSignal:
    return DurableDecisionSignal(
        decision_id=decision.decision_id, run_id=decision.run_id,
        expected_session_id="session-react", nonce=decision.nonce,
        expected_version=0,
        allow=bool(response.get("allow", response.get("approved", True))),
        response_schema_version=1, response=dict(response),
        domain_kind=decision.domain_kind, domain_id=decision.domain_id,
        call_id=decision.call_id, effect_id=decision.effect_id,
        tool_name=decision.tool_name, args_hash=decision.args_hash,
        capability_hash=decision.capability_hash, scope_hash=decision.scope_hash,
    )


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


async def _collect(iterator: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in iterator]


async def _pending(coordinator, parent_run_id):
    records = await coordinator._store.list_pending_child_signals(parent_run_id)
    return tuple(ChildReconciler._signal(record) for record in records)


def _request(run_id: str = "run-react") -> DriverStart:
    spec = replace(_run_spec(run_id), persistence_level=PersistenceLevel.EPHEMERAL)
    return DriverStart(
        run_id=run_id,
        session_id="session-react",
        canonical_messages=({"role": "user", "content": "do it"},),
        session_projection_cursor=7,
        prepared_context_ref="prepared-context-v1",
        tool_set_snapshot_ref="tool-set-v1",
        provider_state={"provider": "primary"},
        iteration=2,
        completion_state={"stop_reason": None},
        run_context=spec.context,
        run_spec=spec,
    )


def _run_spec(run_id: str) -> RunCreate:
    request_id = f"request-{run_id}"
    turn_id = f"turn-{run_id}"
    return RunCreate(
        run_id=run_id,
        idempotency_key=root_idempotency_key("session-react", request_id, turn_id),
        context=RunContext(
            session_id="session-react",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=request_id,
            turn_id=turn_id,
            venue="text",
            workspace={"root": "F:/workspace"},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id=f"trace-{run_id}",
            principal_id="principal-react",
        ),
        payload_fingerprint=fingerprint_json({"prompt": "do it"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="test-only",
        persistence_level=PersistenceLevel.DURABLE,
    )


class ScriptedCollaborator:
    def __init__(
        self,
        start: Iterable[ReactEmission] = (),
        resumes: Iterable[Iterable[ReactEmission]] = (),
    ) -> None:
        self.start_emissions = list(start)
        self.resume_emissions = [list(items) for items in resumes]
        self.resume_inputs: list[tuple[Any, Mapping[str, Any]]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.closed = False

    async def start(self, request: DriverStart) -> AsyncIterator[ReactEmission]:
        for emission in self.start_emissions:
            yield emission

    async def resume(
        self, boundary, response: Mapping[str, Any]
    ) -> AsyncIterator[ReactEmission]:
        self.resume_inputs.append((boundary, dict(response)))
        emissions = self.resume_emissions.pop(0) if self.resume_emissions else []
        for emission in emissions:
            yield emission

    async def cancel(self, run_id: str, reason: str) -> None:
        self.cancelled.append((run_id, reason))

    async def close(self) -> None:
        self.closed = True


class PolicyRegistry:
    def prepared_execution_policy(self, call: PreparedToolCall) -> tuple[bool, bool]:
        return call.effect_type == "staged_file", call.effect_type != "idempotent_read"

    @staticmethod
    def resolve_prepared_spec(call: PreparedToolCall):
        del call
        return SimpleNamespace(permission_category="write_file")


_EFFECTS: dict[str, str] = {}
_OUTCOME_INDEXES: dict[int, int] = {}
_OUTCOME_STATUSES: dict[int, OutcomeStatus] = {}
_OUTCOME_METADATA: dict[int, Mapping[str, Any]] = {}


def prepared_call(*, tool_name: str, model_args: Mapping[str, Any], call_id: str,
                  effect_id: str, capability_hash: str, scope_hash: str,
                  requires_authorization: bool=False, recoverable_effect: bool=False,
                  tool_spec_version: str="1", schema_hash: str="schema",
                  permission_policy_version: str="1") -> PreparedToolCall:
    del capability_hash, scope_hash
    _EFFECTS[call_id] = effect_id
    return PreparedToolCall.prepare(
        tool_name=tool_name, stable_call_id=call_id, final_params=model_args,
        tool_spec_version=tool_spec_version or "1", schema_hash=schema_hash or "schema",
        permission_policy_version=permission_policy_version or "1",
        effect_type="staged_file" if requires_authorization else "opaque_manual" if recoverable_effect else "idempotent_read",
    )


def _call(index: int, *, durable: bool) -> PreparedToolCall:
    return prepared_call(
        tool_name=f"tool_{index}",
        model_args={"index": index},
        call_id=f"call-{index}",
        effect_id=f"effect-{index}",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        recoverable_effect=durable,
        tool_spec_version="1",
        schema_hash=fingerprint_json({"type": "object"}),
        permission_policy_version="1",
    )


def _artifact_call(
    tmp_path,
    index: int,
    *,
    output_name: str | None = None,
    model_args: Mapping[str, Any] | None = None,
) -> PreparedToolCall:
    call_id = f"artifact-call-{index}"
    _EFFECTS[call_id] = f"artifact-effect-{index}"
    target = tmp_path / (
        output_name
        or f"doc_create-session-react-{call_id}.docx"
    )
    return PreparedToolCall.prepare(
        tool_name="doc_create",
        stable_call_id=call_id,
        final_params={
            "spec": dict(model_args or {"title": f"version {index}"}),
            "output_path": str(target),
        },
        prepared_targets=(
            PreparedTarget.prepare(
                target,
                run_id="run-react",
                stable_call_id=call_id,
                mode=TargetMode.CREATE,
                format="docx",
            ),
        ),
        tool_spec_version="1",
        schema_hash=fingerprint_json({"type": "object"}),
        permission_policy_version="1",
        effect_type="idempotent_read",
    )


def _context(call: PreparedToolCall) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope-react",
        session_id="session-react",
        request_id="request-run-react",
        origin="agent",
        root_run_id="run-react",
        parent_run_id=None,
        turn_id="turn-run-react",
        venue="text",
        workspace="F:/workspace",
        write_scope_root="F:/workspace",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        provider_plan=("fixture",),
        run_id="run-react",
        call_id=call.stable_call_id,
        effect_id=_EFFECTS[call.stable_call_id],
        trace_id="trace-run-react",
    )


def _outcome(
    call: PreparedToolCall,
    *,
    status: OutcomeStatus = OutcomeStatus.SUCCEEDED,
    artifact_refs: tuple[str, ...] = (),
) -> NormalizedToolOutcome:
    outcome = (NormalizedToolOutcome.success({"completed": call.stable_call_id})
               if status in {OutcomeStatus.SUCCEEDED, OutcomeStatus.ACCEPTED}
               else NormalizedToolOutcome.malformed("unknown") if status is OutcomeStatus.UNKNOWN
               else NormalizedToolOutcome.failure("tool_failed", "tool failed"))
    suffix = call.stable_call_id.rsplit('-', 1)[-1]
    _OUTCOME_INDEXES[id(outcome)] = int(suffix) if suffix.isdigit() else 0
    _OUTCOME_STATUSES[id(outcome)] = status
    _OUTCOME_METADATA[id(outcome)] = {"receipt_ref": f"receipt:{_EFFECTS[call.stable_call_id]}", "artifact_refs": list(artifact_refs)}
    return outcome


def ToolOutcomesSignal(run_id: str, command_id: str, outcomes: tuple[NormalizedToolOutcome, ...]):
    indexes = tuple(_OUTCOME_INDEXES[id(outcome)] for outcome in outcomes)
    statuses = tuple(_OUTCOME_STATUSES[id(outcome)] for outcome in outcomes)
    metadata = tuple(_OUTCOME_METADATA[id(outcome)] for outcome in outcomes)
    return CanonicalToolOutcomesSignal(run_id, command_id, outcomes, statuses, indexes, metadata)


async def _driver(
    tmp_path,
    collaborator,
    *,
    reader=None,
    reconciler=None,
    auto_mode_check=None,
):
    path = tmp_path / "workflow.db"
    store = SqliteExecutionUnitOfWork(path)
    await store.activate_runtime()
    return (
        _react_driver(
            collaborator,
            store,
            auto_mode_check=auto_mode_check,
        ),
        store,
        path,
    )


async def _recovery_lease(store):
    return await store.recovery_scope("run-react", owner="react-test-recovery")


@pytest.mark.asyncio
async def test_close_releases_collaborator_and_volatile_boundaries(tmp_path) -> None:
    collaborator = ScriptedCollaborator()
    driver, _, _ = await _driver(tmp_path, collaborator)
    active = driver._live.get("run-react")
    assert active is not None
    active.driver_state = ReactCommandBoundary(
        "run-react", "session-react", "command", "execute_tools", (), 0,
        None, None, (), (), (), {}, 0, {},
    )

    await driver.close()

    assert collaborator.closed is True
    assert active.driver_state is None


@pytest.mark.asyncio
async def test_legacy_cancel_aclos_runs_outside_live_lock() -> None:
    live = BoundedLiveIndex()
    active = live.add("run-react", _actor())
    seen: list[bool] = []

    class Iterator:
        async def aclose(self) -> None:
            seen.append(live.lock.locked())
            active.driver_iterator = replacement

    iterator = Iterator()
    replacement = object()
    active.driver_iterator = iterator
    collaborator = AgentLoopCollaborator(_factory(object()))
    collaborator.bind_live_index(live)

    await collaborator.cancel("run-react", "test")

    assert seen == [False]
    assert active.driver_iterator is replacement


@pytest.mark.asyncio
async def test_prepare_recovery_rebuilds_collaborator_before_driver_runs() -> None:
    boundary = ReactCommandBoundary(
        "run-react", "session-react", "command", "provider_launch", (), 0,
        None, None, (), (), (), {}, 0, {},
    )
    seen = []

    class RecoveryCollaborator(ScriptedCollaborator):
        async def prepare_recovery(self, value) -> None:
            seen.append(value)

    driver = _react_driver(RecoveryCollaborator(), SimpleNamespace())

    async def load_boundary(run_id):
        assert run_id == "run-react"
        return boundary

    driver._load_boundary = load_boundary
    await driver.prepare_recovery("run-react", SimpleNamespace())

    assert seen == [boundary]


@pytest.mark.asyncio
async def test_initial_provider_boundary_recovers_without_recreating_root(
    tmp_path,
) -> None:
    first, store, _ = await _driver(tmp_path, ScriptedCollaborator())
    spec = _run_spec("run-react")
    await store.create(spec)
    initial = ReactCommandBoundary(
        run_id="run-react",
        session_id="session-react",
        command_id="root-start:run-react",
        command_kind="provider_start",
        canonical_messages=({"role": "user", "content": "do it"},),
        session_projection_cursor=0,
        prepared_context_ref="prepared-context-v1",
        tool_set_snapshot_ref="tool-set-v1",
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        provider_state={"provider": "fixture"},
        iteration=0,
        completion_state={},
        run_context=spec.context,
        run_spec=spec,
    )
    saved = await store.persist_react_boundary(
        "run-react",
        0,
        initial.to_payload(),
    )
    await first.close()

    collaborator = ScriptedCollaborator(
        resumes=[[ReactFinal("recovered from initial provider boundary")]]
    )
    restarted = _react_driver(collaborator, store)
    candidates = await _collect(
        restarted.recover("run-react", await _recovery_lease(store))
    )

    assert candidates == [
        DriverTerminalCandidate(
            "run-react",
            "completed",
            "recovered from initial provider boundary",
        )
    ]
    assert len(collaborator.resume_inputs) == 1
    recovered_boundary, response = collaborator.resume_inputs[0]
    assert recovered_boundary.command_kind == "provider_start"
    assert recovered_boundary.version > saved.version
    assert recovered_boundary.canonical_messages == (
        {"role": "user", "content": "do it"},
    )
    assert response["type"] == "tool_outcomes"


@pytest.mark.asyncio
async def test_user_continuation_is_root_local_durable_and_resumes_same_driver(
    tmp_path,
) -> None:
    collaborator = ScriptedCollaborator(resumes=[()])
    driver, store, _ = await _driver(tmp_path, collaborator)
    spec = _run_spec("run-react")
    await store.create(spec)
    resolver = TaskWorkContextResolver(tmp_path / "tasks")
    task_scope_id = resolver.task_scope_id(
        "session-react",
        spec.context.request_id,
        spec.context.turn_id,
    )
    work = resolver.resolve(
        session_id="session-react",
        root_run_id="run-react",
        task_scope_id=task_scope_id,
        explicit_workspace=tmp_path / "tasks" / "run-react",
    )
    conversation = resolver.conversation_boundary(
        work, ("request:seed",)
    )
    await store.create_task_context(
        work,
        conversation,
        resolver.projection(work),
    )
    boundary = ReactCommandBoundary(
        run_id="run-react",
        session_id="session-react",
        command_id="provider-start",
        command_kind="provider_launch",
        canonical_messages=(
            {"role": "user", "content": "seed"},
        ),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=None,
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        provider_state={},
        iteration=0,
        completion_state={},
        run_context=spec.context,
        run_spec=spec,
    )
    saved = await store.persist_react_boundary(
        "run-react",
        0,
        boundary.to_payload(),
    )

    events = await _collect(
        driver.signal(
            UserContinuationSignal(
                "run-react",
                task_scope_id=task_scope_id,
                message_ref="request:continue-1",
                content="continue only this root",
                expected_boundary_version=1,
            )
        )
    )

    assert any(event.kind == "persisted_event" for event in events)
    assert len(collaborator.resume_inputs) == 1
    resumed_boundary, resume_signal = collaborator.resume_inputs[0]
    assert resume_signal["type"] == "user_continuation"
    assert resumed_boundary.version > saved.version
    assert resumed_boundary.canonical_messages[-1] == {
        "role": "user",
        "content": "continue only this root",
        "_deskpet_message_ref": "request:continue-1",
    }
    stored_conversation = await store.get_conversation_boundary(
        "run-react"
    )
    assert stored_conversation is not None
    assert stored_conversation.continuation_message_refs == (
        "request:continue-1",
    )


@pytest.mark.asyncio
async def test_queued_user_continuation_keeps_a_recoverable_resume_marker(
    tmp_path,
) -> None:
    collaborator = ScriptedCollaborator(resumes=[()])
    driver, store, _ = await _driver(tmp_path, collaborator)
    spec = _run_spec("run-react")
    await store.create(spec)
    resolver = TaskWorkContextResolver(tmp_path / "tasks")
    task_scope_id = resolver.task_scope_id(
        "session-react", spec.context.request_id, spec.context.turn_id
    )
    work = resolver.resolve(
        session_id="session-react",
        root_run_id="run-react",
        task_scope_id=task_scope_id,
        explicit_workspace=tmp_path / "tasks" / "run-react",
    )
    conversation = resolver.conversation_boundary(
        work, ("request:seed",)
    )
    await store.create_task_context(
        work, conversation, resolver.projection(work)
    )
    boundary = ReactCommandBoundary(
        run_id="run-react",
        session_id="session-react",
        command_id="provider-start",
        command_kind="provider_launch",
        canonical_messages=({"role": "user", "content": "seed"},),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=None,
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        provider_state={},
        iteration=0,
        completion_state={},
        run_context=spec.context,
        run_spec=spec,
    )
    await store.persist_react_boundary(
        "run-react", 0, boundary.to_payload()
    )
    reserved, queued, _ = await store.enqueue_user_continuation(
        "run-react",
        "request:queued-1",
        "steer while running",
        task_scope_id=task_scope_id,
        expected_boundary_version=conversation.version,
    )

    await _collect(
        driver.signal(
            UserContinuationSignal(
                "run-react",
                task_scope_id=task_scope_id,
                message_ref=queued.message_ref,
                content=queued.content,
                expected_boundary_version=reserved.version,
                queued=True,
            )
        )
    )
    saved = await store.load_continuation("run-react")
    assert saved is not None
    assert saved.payload["completion_state"]["pending_resume_signal"] == {
        "type": "user_continuation",
        "message_ref": "request:queued-1",
    }
    assert (
        await store.get_user_continuation(
            "run-react", "request:queued-1"
        )
    ).status == "bound"

    recovery_collaborator = ScriptedCollaborator(
        resumes=[(ReactFinal("recovered answer"),)]
    )
    restarted_driver, restarted_store, _ = await _driver(
        tmp_path, recovery_collaborator
    )
    lease = await _recovery_lease(restarted_store)
    events = await _collect(restarted_driver.recover("run-react", lease))

    assert len(recovery_collaborator.resume_inputs) == 1
    assert recovery_collaborator.resume_inputs[0][1] == {
        "type": "user_continuation",
        "message_ref": "request:queued-1",
    }
    assert events[-1].kind == "terminal"


async def _record_effect(path, call: PreparedToolCall, outcome: NormalizedToolOutcome, status: str) -> None:
    if status == "unknown":
        handoff_state = "started_may_complete"
        completion_disposition = "inflight_effect_may_complete"
    else:
        handoff_state = "reconciled"
        completion_disposition = "normal"
    async with aiosqlite.connect(path) as db:
        await db.execute("""INSERT INTO execution_effects(
            effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
            args_hash,capability_hash,scope_hash,effect_type,status,
            handoff_state,completion_disposition,policy_json,
            prepared_json,outcome_json,artifact_refs_json,effect_version,created_at,updated_at,ended_at)
            VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,'{}',?,?,'[]',1,1,1,1)""",
            (_EFFECTS[call.stable_call_id], "run-react", "fp", call.stable_call_id,
             call.tool_name, call.args_hash, CAPABILITY_HASH, SCOPE_HASH, call.effect_type,
             status, handoff_state, completion_disposition,
             json.dumps(call.to_dict()), json.dumps(outcome.to_dict())))
        await db.commit()


class _ChildLauncher:
    async def _accept_precreated_child(self, command) -> None:
        return None

    async def _deliver_child_signal(self, parent, delivery, recovery_lease) -> None:
        return None


class _CaptureLoop:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, Any]]] = []

    async def _events(self):
        from agent.agent_loop import FinalEvent
        yield FinalEvent(content="continued")

    def run(self, messages, **kwargs):
        self.messages.append(messages)
        return self._events()


async def _schedule_child(store, command):
    parent = await store.query(
        RunRef("run-react", "session-react"),
        ActorContext("principal-react", "session-react", 0),
    )
    coordinator = ChildRunCoordinator(store)
    committed = await coordinator.submit(parent, command)
    await ChildReconciler(
        coordinator, _ChildLauncher(), owner="child-scheduler"
    ).reconcile_once()
    accepted = (await _pending(coordinator, "run-react"))[0]
    return coordinator, committed, accepted


@pytest.mark.asyncio
async def test_driver_preserves_first_token_fallback_and_only_emits_terminal_candidate(tmp_path):
    collaborator = ScriptedCollaborator(
        [
            ReactToken("first"),
            ReactFallback("primary", "backup", "timeout"),
            ReactToken("second"),
            ReactFinal("done"),
        ]
    )
    driver, store, _ = await _driver(tmp_path, collaborator)

    candidates = await _collect(driver.start(_request()))

    assert candidates == [
        TokenCandidate("run-react", "first"),
        ProviderFallbackCandidate("run-react", "primary", "backup", "timeout"),
        TokenCandidate("run-react", "second"),
        DriverTerminalCandidate("run-react", "completed", "done"),
    ]
    assert await store.load_continuation("run-react") is None


@pytest.mark.asyncio
async def test_workspace_precondition_failure_returns_to_model_without_killing_run(
    tmp_path,
):
    call = prepared_call(
        tool_name="workflow_spawn",
        model_args={
            "profile_key": "workflow.durable_task",
            "objective": "create a project",
        },
        call_id="spawn-without-project-directory",
        effect_id="effect-spawn-without-project-directory",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )

    class Collaborator(ScriptedCollaborator):
        async def prepare_control(self, request, prepared):
            del request, prepared
            raise ValueError(
                "project_workspace_selection_required: call "
                "project_directory_select first"
            )

    collaborator = Collaborator(
        [
            ReactControlBatch(
                "control-workspace-precondition",
                call,
                _context(call),
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": call.stable_call_id}],
                    },
                ),
            )
        ],
        resumes=[[ReactFinal("I will ask for the project directory first.")]],
    )
    driver, store, _ = await _driver(tmp_path, collaborator)

    events = await _collect(driver.start(_request()))

    assert events[-1] == DriverTerminalCandidate(
        "run-react",
        "completed",
        "I will ask for the project directory first.",
    )
    assert len(collaborator.resume_inputs) == 1
    response = collaborator.resume_inputs[0][1]
    assert response["type"] == "tool_outcomes"
    boundary = collaborator.resume_inputs[0][0]
    assert boundary.outcomes[0].error["code"] == (
        "project_workspace_selection_required"
    )
    persisted = await store.load_continuation("run-react")
    assert persisted is not None
    persisted_boundary = ReactCommandBoundary.from_record(persisted)
    assert persisted_boundary.outcomes[0].error["code"] == (
        "project_workspace_selection_required"
    )


@pytest.mark.asyncio
async def test_decision_boundary_survives_new_driver_and_keeps_session(tmp_path):
    decision = OpenDecision(
        run_id="run-react",
        command_id="decision-command",
        decision_id="decision-1",
        nonce="nonce-1",
        kind="clarification",
        prompt={"question": "which folder?"},
    )
    first, store, path = await _driver(
        tmp_path, ScriptedCollaborator([decision])
    )
    assert await _collect(first.start(_request())) == [decision]
    record = await store.load_continuation("run-react")
    persisted = ReactCommandBoundary.from_record(record) if record else None
    assert persisted is not None
    assert persisted.session_id == "session-react"
    assert persisted.pending_decision == decision

    resumed = ScriptedCollaborator(resumes=[[ReactFinal("answered")]])
    second = _react_driver(resumed, store)
    response = {"answer": "F:/workspace"}
    signal = DecisionSignal(
        "run-react", "decision-1", response, nonce="nonce-1", version=0,
    )
    candidates = await _collect(
        second.signal_decision_atomically(
            signal, _durable_signal(decision, response), _actor(),
        )
    )

    assert candidates[-1] == DriverTerminalCandidate("run-react", "completed", "answered")
    assert candidates[0].kind == "persisted_event"
    boundary, response = resumed.resume_inputs[0]
    assert boundary.session_id == "session-react"
    assert response["type"] == "decision"
    assert json.loads(boundary.canonical_messages[-1]["content"])["decision_id"] == (
        "decision-1"
    )

    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT status FROM execution_decisions WHERE decision_id='decision-1'"
            )
        ).fetchone()
    assert row == ("allowed",)


@pytest.mark.asyncio
async def test_permission_batch_waits_for_durable_grant_before_execution(tmp_path):
    call = _call(0, durable=True)
    call = prepared_call(
        tool_name=call.tool_name,
        model_args={
            **dict(call.final_params),
            "plan_steps": [{"title": "inspect"}, {"title": "verify"}],
        },
        call_id=call.stable_call_id,
        effect_id=_EFFECTS[call.stable_call_id],
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        requires_authorization=True,
        recoverable_effect=True,
    )
    batch = ReactToolBatch("batch-permission", (call,), (_context(call),))
    driver, store, path = await _driver(tmp_path, ScriptedCollaborator([batch]))

    first = await _collect(driver.start(_request()))
    assert len(first) == 1
    decision = first[0]
    assert decision.kind == "open_decision"
    assert decision.prompt["arguments"] == call.arguments_json()
    assert decision.prompt["arguments"]["plan_steps"] == [
        {"title": "inspect"},
        {"title": "verify"},
    ]
    response = {"allow": True}
    signal = DecisionSignal(
        "run-react", decision.decision_id, response,
        nonce=decision.nonce, version=0,
    )
    resumed = await _collect(
        driver.signal_decision_atomically(
            signal, _durable_signal(decision, response), _actor(),
        )
    )

    assert len(resumed) == 2
    assert resumed[0].kind == "persisted_event"
    command = resumed[1]
    assert command.kind == "execute_tools"
    assert command.grant_refs[0].grant_id
    assert command.grant_refs[0].decision_nonce == decision.nonce


@pytest.mark.asyncio
async def test_denied_control_permission_never_prepares_delegate(tmp_path):
    call = prepared_call(
        tool_name="workflow_spawn",
        model_args={
            "profile_key": "workflow.durable_task",
            "objective": "return READY",
        },
        call_id="denied-workflow-spawn",
        effect_id="effect-denied-workflow-spawn",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        requires_authorization=True,
        recoverable_effect=True,
    )

    class Collaborator(ScriptedCollaborator):
        async def prepare_control(self, request, prepared):
            del request, prepared
            raise AssertionError("denied workflow_spawn must not be prepared")

    collaborator = Collaborator(
        [
            ReactControlBatch(
                "control-denied-workflow-spawn",
                call,
                _context(call),
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": call.stable_call_id}],
                    },
                ),
            )
        ],
        resumes=[[ReactFinal("permission denial handled")]],
    )
    driver, store, _ = await _driver(tmp_path, collaborator)

    decision = (await _collect(driver.start(_request())))[0]
    response = {"decision": "deny"}
    durable_signal = replace(
        _durable_signal(decision, response),
        allow=False,
    )
    resumed = await _collect(
        driver.signal_decision_atomically(
            DecisionSignal(
                "run-react",
                decision.decision_id,
                response,
                nonce=decision.nonce,
                version=0,
            ),
            durable_signal,
            _actor(),
        )
    )

    assert [item.kind for item in resumed] == ["persisted_event", "terminal"]
    assert resumed[-1] == DriverTerminalCandidate(
        "run-react", "completed", "permission denial handled"
    )
    boundary, resume_signal = collaborator.resume_inputs[0]
    assert resume_signal["type"] == "tool_outcomes"
    assert boundary.pending_delegate is None
    assert boundary.outcomes[0].error == {
        "code": "authorization_denied",
        "message": "authorization denied",
    }
    stored = await store.load_continuation("run-react")
    assert stored is not None
    persisted = ReactCommandBoundary.from_record(stored)
    assert persisted.pending_delegate is None


@pytest.mark.asyncio
async def test_first_permission_resolution_atomically_opens_next_permission(tmp_path):
    calls = []
    for index in range(2):
        base = _call(index, durable=True)
        calls.append(prepared_call(
            tool_name=base.tool_name, model_args=dict(base.final_params),
            call_id=base.stable_call_id, effect_id=_EFFECTS[base.stable_call_id],
            capability_hash=CAPABILITY_HASH, scope_hash=SCOPE_HASH,
            requires_authorization=True, recoverable_effect=True,
        ))
    batch = ReactToolBatch(
        "batch-two-permissions", tuple(calls),
        tuple(_context(call) for call in calls),
    )
    driver, store, _ = await _driver(tmp_path, ScriptedCollaborator([batch]))
    first = (await _collect(driver.start(_request())))[0]
    response = {"allow": True}
    signal = DecisionSignal(
        "run-react", first.decision_id, response,
        nonce=first.nonce, version=0,
    )

    resumed = await _collect(driver.signal_decision_atomically(
        signal, _durable_signal(first, response), _actor(),
    ))

    assert [item.kind for item in resumed] == ["persisted_event", "open_decision"]
    second = resumed[1]
    assert second.decision_id != first.decision_id
    boundary = await store.load_continuation("run-react")
    assert boundary is not None and boundary.pending_decision_id == second.decision_id
    assert (await store.get_decision(first.decision_id, ref=RunRef("run-react", "session-react"), actor=_actor())).status.value == "allowed"
    assert (await store.get_decision(second.decision_id, ref=RunRef("run-react", "session-react"), actor=_actor())).status.value == "open"


@pytest.mark.asyncio
async def test_external_action_wait_survives_restart_and_resumes_same_attempt(
    tmp_path,
):
    call = prepared_call(
        tool_name=EXTERNAL_ACTION_WAIT,
        model_args={
            "wait_kind": "uac",
            "required_action": "在 Windows 提示中确认允许 Godot 启动。",
            "evidence_refs": ["evidence:uac-visible"],
        },
        call_id="external-wait-call",
        effect_id="external-wait-effect",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )
    batch = ReactToolBatch(
        "batch-external-wait",
        (call,),
        (_context(call),),
    )
    first, store, _ = await _driver(
        tmp_path, ScriptedCollaborator([batch])
    )

    initial = await _collect(first.start(_request()))

    assert [item.kind for item in initial] == ["open_decision"]
    decision = initial[0]
    assert decision.decision_kind == "workflow_hitl"
    assert decision.domain_kind == "external"
    assert decision.domain_id == "windows_uac"
    record = await store.load_continuation("run-react")
    assert record is not None
    paused_boundary = ReactCommandBoundary.from_record(record)
    wait_state = dict(paused_boundary.completion_state["external_wait"])
    attempt_id = str(
        paused_boundary.completion_state["current_attempt"]["attempt_id"]
    )
    paused_attempt = await store.get_attempt(attempt_id)
    assert paused_attempt is not None
    assert paused_attempt.status.value == "waiting_external"
    assert paused_attempt.budget_eligible is False
    assert await store.list_task_failure_reports(attempt_id) == ()

    resumed_collaborator = ScriptedCollaborator(
        resumes=[[ReactFinal("Godot 已继续启动")]]
    )
    second = _react_driver(resumed_collaborator, store)
    recovered = await _collect(
        second.recover("run-react", await _recovery_lease(store))
    )
    assert recovered == [decision]

    response = {"decision": "complete", "completed": True}
    candidates = await _collect(
        second.signal_decision_atomically(
            DecisionSignal(
                "run-react",
                decision.decision_id,
                response,
                nonce=decision.nonce,
                version=0,
            ),
            _durable_signal(decision, response),
            _actor(),
        )
    )

    assert [item.kind for item in candidates] == [
        "persisted_event",
        "terminal",
    ]
    assert candidates[-1] == DriverTerminalCandidate(
        "run-react", "completed", "Godot 已继续启动"
    )
    wait_record = await store.get_external_wait(
        str(wait_state["record"]["wait_ref"])
    )
    live_attempt = await store.get_attempt(attempt_id)
    assert wait_record is not None
    assert wait_record.state is ExternalWaitState.SATISFIED
    assert live_attempt is not None
    assert live_attempt.attempt_id == attempt_id
    assert live_attempt.plan_version == paused_attempt.plan_version
    assert live_attempt.status.value == "succeeded"
    assert live_attempt.ended_at is not None
    settled_calls = await store.list_provider_action_calls(
        "batch-external-wait"
    )
    assert len(settled_calls) == 1
    assert settled_calls[0].admission_state.value == "settled"
    assert settled_calls[0].terminal_outcome_ref
    async with aiosqlite.connect(store.path) as db:
        settled_batch = await (
            await db.execute(
                """SELECT status,pending_call_count,settled_at
                FROM execution_provider_action_batches
                WHERE provider_batch_id='batch-external-wait'"""
            )
        ).fetchone()
    assert settled_batch is not None
    assert settled_batch[0] == "settled"
    assert settled_batch[1] == 0
    assert settled_batch[2] is not None
    assert await store.list_task_failure_reports(attempt_id) == ()
    resumed_boundary, resume_signal = resumed_collaborator.resume_inputs[0]
    assert resume_signal["type"] == "tool_outcomes"
    tool_messages = [
        item
        for item in resumed_boundary.canonical_messages
        if item.get("role") == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == call.stable_call_id
    tool_payload = json.loads(tool_messages[0]["content"])
    assert tool_payload["outcome"]["state"] == "success"
    assert tool_payload["outcome"]["value"]["completed"] is True
    assert tool_payload["outcome"]["value"]["external_action_handled"] is True
    assert tool_payload["outcome"]["value"]["verification_required"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_workspace_source", ["task_default", "existing"])
@pytest.mark.parametrize("directory_mode", ["create_new", "use_existing"])
async def test_project_directory_selection_rebinds_same_run_before_writes(
    tmp_path,
    initial_workspace_source,
    directory_mode,
):
    default_root = tmp_path / "task-default"
    selected_root = tmp_path / "games" / "apocalypse-demo"
    selected_root.parent.mkdir()
    if directory_mode == "use_existing":
        selected_root.mkdir()
    call = prepared_call(
        tool_name=PROJECT_DIRECTORY_SELECT,
        model_args={
            "project_name": "末日生存 Demo",
            "folder_name": "apocalypse-demo",
            "project_kind": "Godot 游戏",
            "directory_mode": directory_mode,
        },
        call_id="project-directory-call",
        effect_id="project-directory-effect",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )
    collaborator = ScriptedCollaborator(
        [
            ReactToolBatch(
                "batch-project-directory",
                (call,),
                (_context(call),),
            )
        ],
        resumes=[[ReactFinal("开始创建 Godot 项目")]],
    )
    driver, store, _ = await _driver(tmp_path, collaborator)
    request = _request()
    run_context = replace(
        request.run_context,
        workspace={
            "root": str(default_root),
            "write_scope_root": str(default_root),
            "scope_hash": SCOPE_HASH,
        },
        provider_plan={
            "providers": ["relay-cloud"],
            "bindings": [
                {
                    "provider_id": "relay-cloud",
                    "model_id": "kimi-k3",
                }
            ],
        },
    )
    run_spec = replace(
        request.run_spec,
        context=run_context,
        persistence_level=PersistenceLevel.DURABLE,
    )
    task_scope_id = TaskWorkContextResolver.task_scope_id(
        "session-react",
        run_context.request_id,
        run_context.turn_id,
    )
    resolver = TaskWorkContextResolver(tmp_path)
    work = resolver.resolve(
        session_id="session-react",
        root_run_id="run-react",
        task_scope_id=task_scope_id,
        require_workspace=True,
    )
    work = replace(
        work,
        workspace_root=str(default_root.resolve()),
        workspace_source=initial_workspace_source,
    )
    conversation = resolver.conversation_boundary(
        work,
        ("request:project-directory",),
    )
    projection = resolver.projection(work)
    request = replace(
        request,
        run_context=run_context,
        run_spec=run_spec,
        request_payload={
            "task_work_context": {
                "session_id": work.session_id,
                "root_run_id": work.root_run_id,
                "task_scope_id": work.task_scope_id,
                "workspace_root": work.workspace_root,
                "workspace_source": work.workspace_source,
                "binding_version": work.binding_version,
            },
            "conversation_boundary": {
                "boundary_ref": conversation.boundary_ref,
                "session_id": conversation.session_id,
                "root_run_id": conversation.root_run_id,
                "task_scope_id": conversation.task_scope_id,
                "seed_message_refs": conversation.seed_message_refs,
                "continuation_message_refs": (
                    conversation.continuation_message_refs
                ),
                "version": conversation.version,
            },
            "task_run_projection": {
                "projection_id": projection.projection_id,
                "session_id": projection.session_id,
                "root_run_id": projection.root_run_id,
                "task_scope_id": projection.task_scope_id,
                "ui_state": projection.ui_state,
                "version": projection.version,
            },
        },
    )
    await store.create(run_spec)

    initial = await _collect(driver.start(request))
    assert [item.kind for item in initial] == ["open_decision"]
    decision = initial[0]
    assert decision.domain_id == "project_directory"
    assert decision.prompt["folder_name"] == "apocalypse-demo"
    assert decision.prompt["directory_mode"] == directory_mode
    assert decision.prompt["title"] == (
        "选择现有项目目录"
        if directory_mode == "use_existing"
        else "选择项目保存位置"
    )
    assert decision.prompt["required_action"] == (
        "请选择要使用的现有项目文件夹"
        if directory_mode == "use_existing"
        else "请选择项目保存到哪个文件夹下面"
    )
    open_projections = await store.list_open_decision_projections(
        "session-react"
    )
    assert len(open_projections) == 1
    assert open_projections[0]["decision"].decision_id == decision.decision_id
    assert open_projections[0]["root_run_id"] == "run-react"

    response = {
        "decision": "complete",
        "completed": True,
        "project_root": str(selected_root.resolve()),
    }
    candidates = await _collect(
        driver.signal_decision_atomically(
            DecisionSignal(
                "run-react",
                decision.decision_id,
                response,
                nonce=decision.nonce,
                version=0,
            ),
            _durable_signal(decision, response),
            _actor(),
        )
    )

    assert candidates[-1] == DriverTerminalCandidate(
        "run-react",
        "completed",
        "开始创建 Godot 项目",
    )
    rebound = await store.get_task_work_context("run-react")
    assert rebound is not None
    assert rebound.workspace_root == str(selected_root.resolve())
    assert rebound.workspace_source == "user_path"
    assert rebound.binding_version == 2
    resumed_boundary, _ = collaborator.resume_inputs[0]
    assert resumed_boundary.run_context.workspace["root"] == str(
        selected_root.resolve()
    )
    assert thaw_json(resumed_boundary.run_context.provider_plan) == {
        "providers": ["relay-cloud"],
        "bindings": [
            {
                "provider_id": "relay-cloud",
                "model_id": "kimi-k3",
            }
        ],
    }
    tool_message = next(
        item
        for item in resumed_boundary.canonical_messages
        if item.get("role") == "tool"
    )
    value = json.loads(tool_message["content"])["outcome"]["value"]
    assert value["project_directory_selected"] is True
    assert value["project_root"] == str(selected_root.resolve())
    assert await store.list_open_decision_projections("session-react") == ()


@pytest.mark.asyncio
async def test_external_wait_recovery_closes_boundary_then_stage_crash_window(
    tmp_path,
):
    call = prepared_call(
        tool_name=EXTERNAL_ACTION_WAIT,
        model_args={
            "wait_kind": "credential",
            "required_action": "Sign in to the external application.",
        },
        call_id="external-stage-crash-call",
        effect_id="external-stage-crash-effect",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )
    first, store, _ = await _driver(
        tmp_path,
        ScriptedCollaborator(
            [
                ReactToolBatch(
                    "batch-external-stage-crash",
                    (call,),
                    (_context(call),),
                )
            ]
        ),
    )
    original_stage = store.stage_external_wait
    crashed = False

    async def fail_once(wait):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("external_wait_after_boundary")
        return await original_stage(wait)

    store.stage_external_wait = fail_once
    with pytest.raises(RuntimeError, match="external_wait_after_boundary"):
        await _collect(first.start(_request()))
    persisted = await store.load_continuation("run-react")
    assert persisted is not None
    boundary = ReactCommandBoundary.from_record(persisted)
    assert boundary.pending_decision is not None
    assert boundary.pending_decision.domain_kind == "external"

    store.stage_external_wait = original_stage
    second = _react_driver(ScriptedCollaborator(), store)
    recovered = await _collect(
        second.recover("run-react", await _recovery_lease(store))
    )

    assert recovered == [boundary.pending_decision]
    wait_state = boundary.completion_state["external_wait"]
    staged = await store.get_external_wait(
        str(wait_state["record"]["wait_ref"])
    )
    assert staged is not None
    assert staged.state is ExternalWaitState.OPEN
    attempt = await store.get_attempt(staged.attempt_id)
    assert attempt is not None
    assert attempt.status.value == "waiting_external"
    assert await store.list_task_failure_reports(staged.attempt_id) == ()


@pytest.mark.asyncio
async def test_mixed_batch_settles_other_calls_then_waits_without_early_failure_set(
    tmp_path,
):
    failed_call = _call(0, durable=False)
    wait_call = prepared_call(
        tool_name=EXTERNAL_ACTION_WAIT,
        model_args={
            "wait_kind": "user_content",
            "required_action": "Add the missing content in the external editor.",
        },
        call_id="mixed-external-wait-call",
        effect_id="mixed-external-wait-effect",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )
    collaborator = ScriptedCollaborator(
        [
            ReactToolBatch(
                "batch-mixed-external",
                (failed_call, wait_call),
                (_context(failed_call), _context(wait_call)),
            )
        ],
        resumes=[[ReactFinal("failure considered after the wait")]],
    )
    driver, store, _ = await _driver(tmp_path, collaborator)

    initial = await _collect(driver.start(_request()))
    assert [item.kind for item in initial] == ["execute_tools"]
    assert initial[0].calls == (failed_call,)
    failed = _outcome(failed_call, status=OutcomeStatus.FAILED)
    waiting = await _collect(
        driver.signal(ToolOutcomesSignal(
            "run-react", "batch-mixed-external", (failed,)
        ))
    )

    assert [item.kind for item in waiting] == ["open_decision"]
    decision = waiting[0]
    boundary_record = await store.load_continuation("run-react")
    assert boundary_record is not None
    boundary = ReactCommandBoundary.from_record(boundary_record)
    attempt_id = str(boundary.completion_state["current_attempt"]["attempt_id"])
    assert await store.list_task_failure_reports(attempt_id) == ()
    assert (await store.get_attempt(attempt_id)).status.value == "waiting_external"

    response = {"decision": "complete", "completed": True}
    resumed = await _collect(
        driver.signal_decision_atomically(
            DecisionSignal(
                "run-react",
                decision.decision_id,
                response,
                nonce=decision.nonce,
                version=0,
            ),
            _durable_signal(decision, response),
            _actor(),
        )
    )

    assert resumed[-1] == DriverTerminalCandidate(
        "run-react",
        "completed",
        "failure considered after the wait",
    )
    reports = await store.list_task_failure_reports(attempt_id)
    assert len(reports) == 1
    assert reports[0].provider_call_id == failed_call.stable_call_id
    assert reports[0].provider_call_id != wait_call.stable_call_id
    tool_messages = [
        item
        for item in collaborator.resume_inputs[0][0].canonical_messages
        if item.get("role") == "tool"
    ]
    assert [item["tool_call_id"] for item in tool_messages] == [
        failed_call.stable_call_id,
        wait_call.stable_call_id,
    ]


@pytest.mark.asyncio
async def test_auto_mode_atomically_allows_every_permission_without_prompt(
    tmp_path,
):
    calls = []
    for index in range(2):
        base = _call(index, durable=True)
        calls.append(
            prepared_call(
                tool_name=base.tool_name,
                model_args=dict(base.final_params),
                call_id=base.stable_call_id,
                effect_id=_EFFECTS[base.stable_call_id],
                capability_hash=CAPABILITY_HASH,
                scope_hash=SCOPE_HASH,
                requires_authorization=True,
                recoverable_effect=True,
            )
        )
    driver, store, _ = await _driver(
        tmp_path,
        ScriptedCollaborator(
            [
                ReactToolBatch(
                    "batch-auto-permissions",
                    tuple(calls),
                    tuple(_context(call) for call in calls),
                )
            ]
        ),
        auto_mode_check=lambda: True,
    )

    events = await _collect(driver.start(_request()))

    assert [item.kind for item in events] == [
        "persisted_event",
        "persisted_event",
        "execute_tools",
    ]
    command = events[-1]
    assert all(item is not None for item in command.grant_refs)
    assert len({item.grant_id for item in command.grant_refs}) == 2
    for item in command.grant_refs:
        decision = await store.get_decision(
            item.decision_id,
            ref=RunRef("run-react", "session-react"),
            actor=_actor(),
        )
        assert decision.status.value == "allowed"


def _scoped_authorized_call(index: int, workspace) -> PreparedToolCall:
    call_id = f"scoped-call-{index}"
    effect_id = fingerprint_json({"effect": call_id})
    _EFFECTS[call_id] = effect_id
    path = workspace / f"result-{index}.txt"
    return PreparedToolCall.prepare(
        tool_name="write_file",
        stable_call_id=call_id,
        final_params={"path": str(path), "content": str(index)},
        tool_spec_version="v1",
        schema_hash=fingerprint_json({"tool": "write_file"}),
        permission_policy_version="v1",
        effect_type="staged_file",
        resource_selectors=(ResourceSelector.filesystem(path, "write"),),
    )


async def _authorization_driver(tmp_path, *, mode: str):
    path = tmp_path / f"authorization-{mode}.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.activate_runtime()
    await initialize_capability_database(path)
    capability_store = CapabilityStore(uow, clock=lambda: 100.0)
    await capability_store.initialize()
    state = await capability_store.get_policy_state()
    if state.mode != mode:
        await capability_store.compare_and_set_policy_mode(
            mode, expected_generation=state.generation
        )
    runtime = PreparedAuthorizationRuntime(
        capability_store,
        clock=lambda: 100.0,
    )
    calls = tuple(_scoped_authorized_call(index, tmp_path) for index in range(2))
    contexts = tuple(
        replace(
            _context(call),
            workspace=str(tmp_path),
            write_scope_root=str(tmp_path),
        )
        for call in calls
    )
    collaborator = ScriptedCollaborator(
        [ReactToolBatch(f"batch-{mode}-task-grant", calls, contexts)]
    )
    driver = _bind_live(
        ReActDriver(
            collaborator,
            uow,
            PolicyRegistry(),
            authorization_runtime=runtime,
        )
    )[0]
    return driver, uow, capability_store, path


@pytest.mark.asyncio
async def test_authorization_policy_auto_never_projects_permission_ui(
    tmp_path,
):
    driver, store, _, path = await _authorization_driver(tmp_path, mode="auto")

    events = await _collect(driver.start(_request()))

    assert [item.kind for item in events] == [
        "persisted_event",
        "persisted_event",
        "execute_tools",
    ]
    assert all(item.kind != "open_decision" for item in events)
    command = events[-1]
    assert all(item is not None for item in command.grant_refs)
    async with aiosqlite.connect(path) as db:
        task_grants = await (
            await db.execute(
                "SELECT source,COUNT(*) FROM task_grants GROUP BY source"
            )
        ).fetchall()
        decisions = await (
            await db.execute(
                """SELECT status,response_json FROM execution_decisions
                ORDER BY created_at,decision_id"""
            )
        ).fetchall()
    assert task_grants == [("policy:auto", 1)]
    assert len(decisions) == 2
    assert all(row[0] == "allowed" for row in decisions)
    assert all(
        json.loads(row[1])["authorization_source"] == "policy:auto"
        for row in decisions
    )
    for grant_ref in command.grant_refs:
        observed = await store.get_decision(
            grant_ref.decision_id,
            ref=RunRef("run-react", "session-react"),
            actor=_actor(),
        )
        assert observed.status.value == "allowed"


@pytest.mark.asyncio
async def test_manual_confirmation_creates_task_grant_and_suppresses_repeat_ui(
    tmp_path,
):
    driver, _, _, path = await _authorization_driver(tmp_path, mode="manual")
    initial = await _collect(driver.start(_request()))
    assert [item.kind for item in initial] == ["open_decision"]
    first = initial[0]
    response = {"allow": True, "source": "user"}

    resumed = await _collect(
        driver.signal_decision_atomically(
            DecisionSignal(
                "run-react",
                first.decision_id,
                response,
                nonce=first.nonce,
                version=0,
            ),
            _durable_signal(first, response),
            _actor(),
        )
    )

    assert [item.kind for item in resumed] == [
        "persisted_event",
        "persisted_event",
        "execute_tools",
    ]
    assert all(item.kind != "open_decision" for item in resumed)
    async with aiosqlite.connect(path) as db:
        task_grants = await (
            await db.execute(
                "SELECT source,COUNT(*) FROM task_grants GROUP BY source"
            )
        ).fetchall()
    assert task_grants == [("user", 1)]


@pytest.mark.asyncio
async def test_recovery_backfills_successful_effect_without_regenerating_or_reexecuting(tmp_path):
    call = _call(0, durable=True)
    batch = ReactToolBatch("batch-1", (call,), (_context(call),))
    first, store, path = await _driver(tmp_path, ScriptedCollaborator([batch]))
    assert (await _collect(first.start(_request())))[0].calls == (call,)

    # The external executor committed the write, then the process crashed
    # before ToolOutcomesSignal could backfill the model transcript.
    committed = _outcome(call)
    await _record_effect(path, call, committed, "succeeded")
    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("recovered")]])
    second = _react_driver(collaborator, store)
    candidates = await _collect(second.recover("run-react", await _recovery_lease(store)))

    assert candidates == [DriverTerminalCandidate("run-react", "completed", "recovered")]
    boundary, response = collaborator.resume_inputs[0]
    assert boundary.outcomes == (committed,)
    assert response == {
        "type": "tool_outcomes",
        "command_id": "batch-1",
        "scoped_evidence": UNKNOWN_EVIDENCE,
    }
    tool_message = boundary.canonical_messages[-1]
    assert tool_message["tool_call_id"] == call.stable_call_id
    assert json.loads(tool_message["content"])["effect_id"] == _EFFECTS[call.stable_call_id]
    record = await store.load_continuation("run-react")
    persisted = ReactCommandBoundary.from_record(record) if record else None
    assert persisted is not None
    assert persisted.completion_state["model_backfilled"] is True


@pytest.mark.asyncio
async def test_same_run_default_artifact_completion_replays_after_argument_change_and_recovery(
    tmp_path,
):
    first_call = _artifact_call(
        tmp_path, 0, model_args={"title": "first draft"}
    )
    driver, _, _ = await _driver(tmp_path, ScriptedCollaborator())
    first_boundary = driver._boundary_for_batch(
        _request(),
        ReactToolBatch(
            "artifact-batch-1",
            (first_call,),
            (_context(first_call),),
        ),
    )
    outcome = NormalizedToolOutcome.success(
        {
            "path": first_call.prepared_targets[0].final_path,
            "artifacts": [{"kind": "file", "path": first_call.prepared_targets[0].final_path}],
        }
    )
    metadata = {
        "receipt_ref": "receipt:word-1",
        "artifact_refs": ["artifact:word-1"],
    }
    completed = first_boundary.with_outcomes(
        {0: outcome},
        {0: OutcomeStatus.SUCCEEDED},
        {0: metadata},
    )
    completed = remember_artifact_completion(
        completed,
        call=first_call,
        outcome=outcome,
        status=OutcomeStatus.SUCCEEDED,
        metadata=metadata,
    )

    recovered = ReactCommandBoundary.from_record(
        SimpleNamespace(
            run_id="run-react",
            version=completed.version,
            payload=completed.to_payload(),
        )
    )
    second_call = _artifact_call(
        tmp_path, 1, model_args={"title": "revised draft", "extra": True}
    )
    replayed = driver._boundary_for_batch(
        recovered.to_start(),
        ReactToolBatch(
            "artifact-batch-2",
            (second_call,),
            (_context(second_call),),
        ),
        recovered.version,
    )

    assert replayed.pending_indexes == ()
    assert replayed.outcomes == (outcome,)
    assert replayed.outcome_statuses == (OutcomeStatus.SUCCEEDED,)
    assert replayed.outcome_metadata[0]["artifact_refs"] == ["artifact:word-1"]
    latch = replayed.outcome_metadata[0]["artifact_completion_latch"]
    assert latch["state"] == "reused"
    assert latch["original_call_id"] == first_call.stable_call_id
    assert latch["target_path"] == first_call.prepared_targets[0].final_path
    assert "do not create another copy" in latch["message"]


@pytest.mark.asyncio
async def test_same_run_artifact_completion_allows_distinct_explicit_paths(
    tmp_path,
):
    first_call = _artifact_call(tmp_path, 0, output_name="version-a.docx")
    second_call = _artifact_call(tmp_path, 1, output_name="version-b.docx")
    driver, _, _ = await _driver(tmp_path, ScriptedCollaborator())
    first_boundary = driver._boundary_for_batch(
        _request(),
        ReactToolBatch(
            "explicit-artifact-1",
            (first_call,),
            (_context(first_call),),
        ),
    )
    outcome = NormalizedToolOutcome.success(
        {"path": first_call.prepared_targets[0].final_path}
    )
    completed = remember_artifact_completion(
        first_boundary,
        call=first_call,
        outcome=outcome,
        status=OutcomeStatus.SUCCEEDED,
        metadata={"artifact_refs": ["artifact:version-a"]},
    )

    distinct = driver._boundary_for_batch(
        completed.to_start(),
        ReactToolBatch(
            "explicit-artifact-2",
            (second_call,),
            (_context(second_call),),
        ),
    )
    repeated = driver._boundary_for_batch(
        completed.to_start(),
        ReactToolBatch(
            "explicit-artifact-repeat",
            (first_call,),
            (_context(first_call),),
        ),
    )

    assert distinct.pending_indexes == (0,)
    assert repeated.pending_indexes == ()
    assert repeated.outcomes == (outcome,)


@pytest.mark.parametrize(
    "corruption",
    [
        {"slot": "excel_create:default"},
        {"tool_name": "excel_create"},
        {"original_call_id": "alien-call"},
        {"target_path": "C:/alien.xlsx"},
        {"metadata": "not-an-object"},
        {
            "outcome": NormalizedToolOutcome.success(
                {"path": "C:/alien.docx"}
            ).to_dict()
        },
    ],
)
def test_same_run_artifact_completion_corruption_fails_open_to_execution(
    tmp_path, corruption
):
    call = _artifact_call(tmp_path, 1)
    original_call_id = "artifact-call-0"
    target_path = str(
        tmp_path
        / f"doc_create-session-react-{original_call_id}.docx"
    )
    latch = {
        "slot": "doc_create:default",
        "tool_name": "doc_create",
        "original_call_id": original_call_id,
        "target_path": target_path,
        "outcome": NormalizedToolOutcome.success(
            {"path": target_path}
        ).to_dict(),
        "status": OutcomeStatus.SUCCEEDED.value,
        "metadata": {
            "receipt_ref": "receipt:word-1",
            "artifact_refs": ["artifact:word-1"],
        },
        **corruption,
    }

    outcomes, statuses, metadata = (
        replay_artifact_completions(
            session_id="session-react",
            calls=(call,),
            completion_state={
                "artifact_completion_latches": {
                    "doc_create:default": latch
                }
            },
        )
    )

    assert outcomes == (None,)
    assert statuses == (None,)
    assert metadata == ({},)


@pytest.mark.parametrize(
    "status",
    [OutcomeStatus.ACCEPTED, OutcomeStatus.FAILED, OutcomeStatus.UNKNOWN],
)
def test_same_run_artifact_completion_only_latches_verified_success(
    tmp_path, status
):
    call = _artifact_call(tmp_path, 0)
    boundary = ReactCommandBoundary(
        run_id="run-react",
        session_id="session-react",
        command_id="artifact-non-success",
        command_kind="execute_tools",
        canonical_messages=(),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=None,
        pending_calls=(call,),
        tool_contexts=(_context(call),),
        outcomes=(None,),
        provider_state={},
        iteration=1,
        completion_state={},
    )
    outcome = (
        NormalizedToolOutcome.success({"accepted": True})
        if status is OutcomeStatus.ACCEPTED
        else NormalizedToolOutcome.malformed("unknown")
        if status is OutcomeStatus.UNKNOWN
        else NormalizedToolOutcome.failure("failed", "failed")
    )

    unchanged = remember_artifact_completion(
        boundary,
        call=call,
        outcome=outcome,
        status=status,
        metadata={},
    )

    assert "artifact_completion_latches" not in unchanged.completion_state


@pytest.mark.asyncio
async def test_completed_tool_uses_exact_effect_scope_for_agent_resume(tmp_path):
    call = _call(0, durable=True)
    batch = ReactToolBatch("batch-evidence", (call,), (_context(call),))
    collaborator = ScriptedCollaborator([batch], resumes=[[ReactFinal("scoped")]])
    driver, _, path = await _driver(tmp_path, collaborator)
    await _collect(driver.start(_request()))
    outcome = _outcome(call, artifact_refs=("artifact://result/1",))
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO execution_effects(
            effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
            args_hash,capability_hash,scope_hash,effect_type,status,
            handoff_state,completion_disposition,policy_json,
            prepared_json,outcome_json,receipt_ref,artifact_refs_json,
            effect_version,created_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_EFFECTS[call.stable_call_id], 1, "run-react", "effect-fingerprint", call.stable_call_id,
             call.tool_name, call.args_hash, CAPABILITY_HASH, SCOPE_HASH, "write",
             "succeeded", "reconciled", "normal", "{}", "{}",
             json.dumps(outcome.to_dict()), _OUTCOME_METADATA[id(outcome)]["receipt_ref"],
             json.dumps(_OUTCOME_METADATA[id(outcome)]["artifact_refs"]), 1, 1.0, 1.0, 1.0),
        )
        await db.commit()

    await _collect(driver.signal(ToolOutcomesSignal("run-react", batch.command_id, (outcome,))))

    selection = collaborator.resume_inputs[0][1]["scoped_evidence"]
    assert selection.status == "matched"
    assert selection.records[0].context == EvidenceContext(
        run_id="run-react", turn_id="turn-run-react", call_id=call.stable_call_id,
        effect_id=_EFFECTS[call.stable_call_id], artifact_ref="artifact://result/1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [OutcomeStatus.FAILED, OutcomeStatus.ACCEPTED])
async def test_non_successful_tool_outcome_fails_closed_without_evidence_lookup(tmp_path, status):
    call = _call(0, durable=True)
    batch = ReactToolBatch("batch-non-success", (call,), (_context(call),))
    collaborator = ScriptedCollaborator([batch], resumes=[[ReactFinal("honest")]])
    driver, store, _ = await _driver(tmp_path, collaborator)
    await _collect(driver.start(_request()))

    async def forbidden_lookup(context):
        raise AssertionError("non-successful outcomes must not query completion evidence")

    store.lookup_completion_evidence = forbidden_lookup
    outcome = _outcome(call, status=status)
    await _collect(driver.signal(ToolOutcomesSignal("run-react", batch.command_id, (outcome,))))
    assert collaborator.resume_inputs[0][1]["scoped_evidence"] is UNKNOWN_EVIDENCE


@pytest.mark.asyncio
async def test_mixed_multi_tool_batch_fails_closed_without_partial_evidence(tmp_path):
    calls = (_call(0, durable=True), _call(1, durable=True))
    batch = ReactToolBatch("mixed-evidence", calls, tuple(_context(call) for call in calls))
    driver, store, _ = await _driver(tmp_path, ScriptedCollaborator())
    boundary = driver._boundary_for_batch(_request(), batch).with_outcomes({
        0: _outcome(calls[0]),
        1: _outcome(calls[1], status=OutcomeStatus.FAILED),
    }, {0: OutcomeStatus.SUCCEEDED, 1: OutcomeStatus.FAILED})

    async def forbidden_lookup(context):
        raise AssertionError("mixed batch must fail closed before partial lookup")

    store.lookup_completion_evidence = forbidden_lookup
    assert await driver._completion_evidence(boundary) is UNKNOWN_EVIDENCE


@pytest.mark.asyncio
async def test_mixed_batch_freezes_full_order_and_recovers_only_missing_calls(tmp_path):
    calls = (_call(0, durable=False), _call(1, durable=True), _call(2, durable=False))
    batch = ReactToolBatch("mixed", calls, tuple(_context(call) for call in calls))
    first, store, path = await _driver(tmp_path, ScriptedCollaborator([batch]))
    initial = await _collect(first.start(_request()))
    assert initial == [ExecuteTools("run-react", "mixed", calls, batch.contexts, (0, 1, 2), effectful=(False, True, False))]
    record = await store.load_continuation("run-react")
    persisted = ReactCommandBoundary.from_record(record) if record else None
    assert persisted is not None
    assert persisted.pending_calls == calls
    attempt = await store.get_attempt(
        str(persisted.completion_state["current_attempt"]["attempt_id"])
    )
    durable_calls = await store.list_provider_action_calls("mixed")
    assert attempt is not None
    assert attempt.provider_batch_id == "mixed"
    assert tuple(item.provider_call_id for item in durable_calls) == (
        "call-0",
        "call-1",
        "call-2",
    )
    assert tuple(item.admission_state.value for item in durable_calls) == (
        "prepared",
        "prepared",
        "prepared",
    )
    assert all(
        item.command_boundary_ref == "react-command:run-react:mixed"
        and item.prepared_call_ref
        for item in durable_calls
    )

    after_first = await _collect(
        first.signal(ToolOutcomesSignal("run-react", "mixed", (_outcome(calls[0]),)))
    )
    assert after_first[0].calls == calls[1:]
    assert after_first[0].original_indexes == (1, 2)
    await _record_effect(path, calls[1], _outcome(calls[1]), "succeeded")

    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("all done")]])
    second = _react_driver(collaborator, store)
    recovered = await _collect(second.recover("run-react", await _recovery_lease(store)))
    assert recovered[0].calls == (calls[2],)
    assert recovered[0].original_indexes == (2,)

    finished = await _collect(
        second.signal(ToolOutcomesSignal("run-react", "mixed", (_outcome(calls[2]),)))
    )
    assert finished == [DriverTerminalCandidate("run-react", "completed", "all done")]
    boundary = collaborator.resume_inputs[0][0]
    assert tuple(message["tool_call_id"] for message in boundary.canonical_messages[-3:]) == (
        "call-0",
        "call-1",
        "call-2",
    )


@pytest.mark.asyncio
async def test_unknown_effect_is_observed_without_driver_side_reconcile(tmp_path):
    call = _call(0, durable=True)
    batch = ReactToolBatch("batch-unknown", (call,), (_context(call),))
    first, store, path = await _driver(tmp_path, ScriptedCollaborator([batch]))
    await _collect(first.start(_request()))
    unknown = _outcome(call, status=OutcomeStatus.UNKNOWN)
    await _record_effect(path, call, unknown, "unknown")
    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("uncertain")]])
    second = _react_driver(collaborator, store)

    candidates = await _collect(second.recover("run-react", await _recovery_lease(store)))

    assert candidates == [DriverTerminalCandidate("run-react", "completed", "uncertain")]
    assert collaborator.resume_inputs[0][0].outcome_statuses == (OutcomeStatus.UNKNOWN,)


@pytest.mark.asyncio
async def test_delegate_boundary_survives_restart_and_accept_signal(tmp_path):
    command = _delegate(JoinPolicy.DETACHED)
    first, store, path = await _driver(
        tmp_path,
        ScriptedCollaborator([command]),
    )
    assert await _collect(first.start(_request())) == [command]
    record = await store.load_continuation("run-react")
    persisted = ReactCommandBoundary.from_record(record) if record else None
    assert persisted is not None and persisted.pending_delegate == command

    resumed = ScriptedCollaborator(resumes=[[ReactFinal("detached accepted")]])
    second = _react_driver(resumed, store)
    assert await _collect(second.recover("run-react", await _recovery_lease(store))) == [command]
    candidates = await _collect(
        second.signal(ChildAcceptedSignal("run-react", command.command_id, "child-1"))
    )
    assert candidates == [
        ChildAcceptedCandidate(
            "run-react", command.command_id, "child-1", JoinPolicy.DETACHED
        ),
        DriverTerminalCandidate("run-react", "completed", "detached accepted"),
    ]


def _delegate(join_policy: JoinPolicy):
    attachment = {
        JoinPolicy.JOIN_BEFORE_FINAL: AttachmentPolicy.ATTACHED,
        JoinPolicy.ROOT_TERMINAL_CHILD: AttachmentPolicy.ROOT_TERMINAL_CHILD,
        JoinPolicy.DETACHED: AttachmentPolicy.DETACHED,
    }[join_policy]
    return DelegateRun(
        run_id="run-react",
        command_id=f"delegate-{join_policy.value}",
        child_request={"task": "bounded child"},
        route_hint="general",
        capability_subset=("read",),
        attachment_policy=attachment,
        join_policy=join_policy,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("join_policy", list(JoinPolicy))
async def test_delegate_join_policy_contracts(join_policy, tmp_path):
    command = _delegate(join_policy)
    collaborator = ScriptedCollaborator(
        [command],
        resumes=[[ReactFinal(f"resumed-{join_policy.value}")]],
    )
    driver, _, _ = await _driver(tmp_path, collaborator)
    assert await _collect(driver.start(_request())) == [command]

    accepted = await _collect(
        driver.signal(
            ChildAcceptedSignal("run-react", command.command_id, "child-run")
        )
    )
    assert accepted[0] == ChildAcceptedCandidate(
        "run-react", command.command_id, "child-run", join_policy
    )
    if join_policy is JoinPolicy.DETACHED:
        assert accepted[1] == DriverTerminalCandidate(
            "run-react", "completed", "resumed-detached"
        )
        return
    assert len(accepted) == 1

    terminal = await _collect(
        driver.signal(
            ChildTerminalSignal(
                "run-react", command.command_id, "child-run", "completed", "child value"
            )
        )
    )
    if join_policy is JoinPolicy.ROOT_TERMINAL_CHILD:
        assert terminal == [
            DriverTerminalCandidate(
                "run-react",
                "completed",
                "child value",
                correlation={"child_run_id": "child-run"},
            )
        ]
    else:
        assert terminal == [
            DriverTerminalCandidate(
                "run-react", "completed", "resumed-join_before_final"
            )
        ]


@pytest.mark.asyncio
async def test_detached_child_accepted_is_in_real_model_input_once(tmp_path):
    command = _delegate(JoinPolicy.DETACHED)
    first, store, _ = await _driver(tmp_path, ScriptedCollaborator([command]))
    await _collect(first.start(_request()))
    _, _, accepted = await _schedule_child(store, command)
    loop = _CaptureLoop()

    await _collect(_react_driver(AgentLoopCollaborator(_factory(loop)), store).signal(accepted))

    payloads = [json.loads(message["content"]) for message in loop.messages[0] if message["role"] == "system"]
    assert [item["signal"] for item in payloads if item.get("type") == "host_child_response"] == ["child_accepted"]
    assert payloads[-1]["signal_id"] == accepted.signal_id


@pytest.mark.asyncio
async def test_joined_child_terminal_is_in_real_model_input_once(tmp_path):
    command = _delegate(JoinPolicy.JOIN_BEFORE_FINAL)
    first, store, _ = await _driver(tmp_path, ScriptedCollaborator([command]))
    await _collect(first.start(_request()))
    coordinator, committed, accepted = await _schedule_child(store, command)
    await _collect(first.signal(accepted))
    await store.finalize_child_and_enqueue_parent_signal(
        committed.operation_id,
        expected_version=1,
        terminal_status="completed",
        event=RunEventCandidate(event_key="child-done", kind="run.completed", status="succeeded", driver_kind="react"),
        value={"answer": 7},
    )
    terminal = (await _pending(coordinator, "run-react"))[0]
    loop = _CaptureLoop()

    await _collect(_react_driver(AgentLoopCollaborator(_factory(loop)), store).signal(terminal))

    payloads = [json.loads(message["content"]) for message in loop.messages[0] if message["role"] == "system"]
    assert [item["signal"] for item in payloads if item.get("type") == "host_child_response"] == ["child_accepted", "child_terminal"]
    assert payloads[-1]["value"] == {"answer": 7}


@pytest.mark.asyncio
async def test_child_terminal_persisted_before_resume_recovers_into_model_once(tmp_path):
    command = _delegate(JoinPolicy.JOIN_BEFORE_FINAL)
    first, store, _ = await _driver(tmp_path, ScriptedCollaborator([command]))
    await _collect(first.start(_request()))
    coordinator, committed, accepted = await _schedule_child(store, command)
    await _collect(first.signal(accepted))
    await store.finalize_child_and_enqueue_parent_signal(
        committed.operation_id,
        expected_version=1,
        terminal_status="completed",
        event=RunEventCandidate(event_key="child-recovery-done", kind="run.completed", status="succeeded", driver_kind="react"),
        value="durable child",
    )
    terminal = (await _pending(coordinator, "run-react"))[0]

    class CrashOnResume(ScriptedCollaborator):
        async def resume(self, boundary, response):
            raise RuntimeError("crash after child commit")
            yield

    with pytest.raises(RuntimeError, match="after child commit"):
        await _collect(_react_driver(CrashOnResume(), store).signal(terminal))
    loop = _CaptureLoop()
    restarted = _react_driver(AgentLoopCollaborator(_factory(loop)), store)
    await _collect(restarted.recover("run-react", await _recovery_lease(store)))

    payloads = [json.loads(message["content"]) for message in loop.messages[0] if message["role"] == "system"]
    terminal_payloads = [item for item in payloads if item.get("signal") == "child_terminal"]
    assert len(terminal_payloads) == 1
    assert terminal_payloads[0]["signal_id"] == terminal.signal_id


@pytest.mark.asyncio
async def test_root_terminal_child_recovers_after_atomic_ack_before_terminal_materializes(tmp_path):
    command = _delegate(JoinPolicy.ROOT_TERMINAL_CHILD)
    first, store, _ = await _driver(tmp_path, ScriptedCollaborator([command]))
    await _collect(first.start(_request()))
    coordinator, committed, accepted = await _schedule_child(store, command)
    await _collect(first.signal(accepted))
    await store.finalize_child_and_enqueue_parent_signal(
        committed.operation_id,
        expected_version=1,
        terminal_status="completed",
        event=RunEventCandidate(event_key="root-child-done", kind="run.completed", status="succeeded", driver_kind="react"),
        value="root child result",
    )
    terminal = (await _pending(coordinator, "run-react"))[0]
    original_apply = first._apply_child_inbox

    async def crash_after_apply(boundary, signal, *, recovery_lease):
        await original_apply(boundary, signal, recovery_lease=recovery_lease)
        raise RuntimeError("crash before root terminal materializes")

    first._apply_child_inbox = crash_after_apply
    with pytest.raises(RuntimeError, match="before root terminal"):
        await _collect(first.signal(terminal))
    restarted = _react_driver(ScriptedCollaborator(), store)

    assert await _collect(restarted.recover("run-react", await _recovery_lease(store))) == [
        DriverTerminalCandidate(
            "run-react", "completed", "root child result",
            correlation={"child_run_id": terminal.child_run_id},
        )
    ]


@pytest.mark.asyncio
async def test_builder_child_terminal_atomically_stages_same_run_refresh(
    tmp_path,
):
    path = tmp_path / "workflow.db"
    store = SqliteExecutionUnitOfWork(path)
    await store.activate_runtime()
    capability_store = CapabilityStore(store)
    stamp = CatalogStamp(
        catalog_generation=0,
        registry_revision=0,
        binding_generation=0,
        skill_revision=0,
        mcp_revision=0,
    )
    search_ref = fingerprint_json({"search": "builder"})
    lineage = CapabilityBuildLineage(
        root_run_id="run-react",
        parent_run_id="run-react",
        parent_goal_ref="goal-react",
        original_objective="build a photo renamer",
        original_args={"folder": "photos"},
        search_receipt_ref=search_ref,
        catalog_stamp_fingerprint=stamp.fingerprint,
    )
    managed = tmp_path / "managed-builder"
    launch = CapabilityBuildLaunch(
        lineage=lineage,
        search_evidence=CapabilityBuildSearchEvidence(
            receipt_ref=search_ref,
            catalog_stamp=stamp.to_dict(),
            snapshot_ref="catalog:builder",
            query_hash=fingerprint_json({"query": "builder"}),
            hit_count=0,
            best_executable_score=None,
        ),
        task_workspace=str(tmp_path),
        managed_staging_base=str(managed),
        staging_root=str(managed / lineage.lineage_id),
    )
    call = _call(0, durable=True)
    context = _context(call)
    command = DelegateRun(
        run_id="run-react",
        command_id="builder-child-command",
        child_request={
            "driver_kind": "workflow",
            "profile_key": "workflow.capability_build",
            "capability_builder": launch.to_dict(),
        },
        route_hint="workflow.capability_build",
        capability_subset=("write_file",),
        attachment_policy=AttachmentPolicy.ATTACHED,
        join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
    )

    class Collaborator(ScriptedCollaborator):
        async def start(self, request):
            del request
            yield ReactControlBatch(
                "builder-parent-command",
                call,
                context,
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": call.stable_call_id}],
                    },
                ),
            )

        async def prepare_control(self, request, prepared):
            del request, prepared
            return command

    class BuilderHost:
        async def finalize_child_completion(
            self, parsed_launch, terminal_value, *, context
        ):
            assert parsed_launch == launch
            assert terminal_value["lineage_id"] == lineage.lineage_id
            receipt = CapabilityOperationReceipt(
                operation_id="builder-operation",
                root_run_id="run-react",
                parent_command_id="builder-parent-command",
                parent_effect_id=context.effect_id,
                action="build",
                refresh_nonce="builder-refresh-nonce",
                old_stamp=stamp,
                published_binding_generation=1,
                affected_capability_ids=("photo_renamer",),
                affected_version_refs=("photo_renamer@1.0.0:" + "a" * 64,),
                affected_tool_spec_fingerprints=("b" * 64,),
                manifest_hashes=("a" * 64,),
            )
            await capability_store.create_operation(
                operation_id=receipt.operation_id,
                idempotency_key="builder-operation-idempotency",
                kind="build",
                request={"fixture": True},
                root_run_id="run-react",
                pack_id="photo_renamer",
                requested_scope="run",
                requested_scope_key="run-react",
            )
            for phase in CAPABILITY_OPERATION_PHASES:
                await capability_store.commit_phase(
                    receipt.operation_id,
                    phase,
                    evidence={"fixture": True, "phase": phase},
                )
            await capability_store.put_operation_receipt(receipt)
            return {
                "ok": True,
                "operation_id": receipt.operation_id,
                "operation_receipt": receipt.to_dict(),
            }

    driver = ReActDriver(
        Collaborator(),
        store,
        PolicyRegistry(),
        capability_refresh_staging=CapabilityRefreshStagingService(
            capability_store
        ),
        capability_builder_host=BuilderHost(),
    )
    _bind_live(driver)
    request = replace(
        _request(),
        request_payload={
            "task_scope_id": "task-react",
            "capability_refresh": {
                "catalog_stamp": stamp.to_dict(),
                "catalog_snapshot_ref": "c" * 64,
                "tool_set_snapshot_ref": "d" * 64,
                "exposure_intent_ref": "e" * 64,
            },
        },
    )
    assert await _collect(driver.start(request)) == [command]
    coordinator, committed, accepted = await _schedule_child(store, command)
    await _collect(driver.signal(accepted))
    await store.finalize_child_and_enqueue_parent_signal(
        committed.operation_id,
        expected_version=1,
        terminal_status="completed",
        event=RunEventCandidate(
            event_key="builder-child-done",
            kind="run.completed",
            status="succeeded",
            driver_kind="workflow",
        ),
        value={
            "schema_version": 1,
            "lineage_id": lineage.lineage_id,
            "draft_index": 0,
            "draft_path": launch.initial_draft,
        },
    )
    terminal = (await _pending(coordinator, "run-react"))[0]
    record = await store.load_continuation("run-react")
    assert record is not None
    boundary = ReactCommandBoundary.from_record(record)

    updated, persisted = await driver._apply_child_inbox(
        boundary,
        terminal,
        recovery_lease=None,
    )

    assert persisted is not None
    refresh = updated.request_payload["capability_refresh"]
    intent_id = str(refresh["refresh_pending"])
    intent = await capability_store.get_refresh_intent(intent_id)
    assert intent is not None
    assert intent.source_kind == "child_terminal"
    assert intent.expected_continuation_version == updated.version
    assert refresh["provider_backfill_blocked"] is True


@pytest.mark.asyncio
async def test_rejected_control_replan_does_not_launch_another_child(
    tmp_path, monkeypatch
):
    call = _call(0, durable=False)
    canonical = (
        {
            "role": "assistant",
            "tool_calls": [{"id": call.stable_call_id}],
        },
    )
    repeated = ReactControlBatch(
        "control-repeated",
        call,
        _context(call),
        canonical,
    )
    collaborator = ScriptedCollaborator(
        [repeated],
        resumes=[[ReactFinal("replanned without another child")]],
    )
    driver, _, _ = await _driver(tmp_path, collaborator)

    async def skip_provider_admission(_boundary):
        return None

    async def keep_local_failure_set(boundary):
        return boundary

    monkeypatch.setattr(driver, "_admit_provider_boundary", skip_provider_admission)
    monkeypatch.setattr(driver, "_persist_failure_set", keep_local_failure_set)
    first = driver._boundary_for_control(
        _request(),
        ReactControlBatch(
            "control-first",
            call,
            _context(call),
            canonical,
        ),
    ).with_outcomes(
        {0: NormalizedToolOutcome.failure("child_workflow_failed", "failed")},
        {0: OutcomeStatus.FAILED},
    )
    failed = ReActDriver._stage_failure_reports(first)

    candidates = await _collect(driver.start(failed.to_start()))

    assert candidates == [
        DriverTerminalCandidate(
            "run-react", "completed", "replanned without another child"
        )
    ]
    persisted = ReactCommandBoundary.from_record(
        await driver._uow.load_continuation("run-react")
    )
    assert persisted.pending_delegate is None
    assert persisted.outcomes[0] is not None
    assert persisted.outcomes[0].error["code"] == "replan_required"


def _workflow_spawn_call(
    objective: str, *, call_id: str = "workflow-spawn-call"
) -> PreparedToolCall:
    return prepared_call(
        tool_name="workflow_spawn",
        model_args={
            "profile_key": "workflow.durable_task",
            "objective": objective,
            "catalog_generation": 1,
        },
        call_id=call_id,
        effect_id=f"effect-{call_id}",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )


@pytest.mark.asyncio
async def test_failed_child_objective_blocks_paraphrased_redelegation_before_ticket(
    tmp_path,
):
    prior = _workflow_spawn_call(
        "Count every Python file in the repository and report the total",
        call_id="failed-workflow-spawn",
    )
    repeated = _workflow_spawn_call(
        "Please report the total after counting every Python file in the repository",
        call_id="repeated-workflow-spawn",
    )

    class Collaborator(ScriptedCollaborator):
        async def prepare_control(self, request, prepared):
            del request, prepared
            raise AssertionError("duplicate objective must be rejected before ticket issue")

    collaborator = Collaborator(
        [
            ReactControlBatch(
                "control-repeated-objective",
                repeated,
                _context(repeated),
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": repeated.stable_call_id}],
                    },
                ),
            )
        ],
        resumes=[[ReactFinal("reported the existing child failure honestly")]],
    )
    driver, store, _ = await _driver(tmp_path, collaborator)
    signature = driver._delegate_objective_signature(
        prior, route_hint="workflow.durable_task"
    )
    assert signature is not None
    signature.update(child_run_id="child-failed", command_id="delegate-failed")
    request = replace(
        _request(),
        completion_state={
            "stop_reason": None,
            "failed_delegate_objectives": [signature],
        },
    )

    candidates = await _collect(driver.start(request))

    assert candidates == [
        DriverTerminalCandidate(
            "run-react",
            "completed",
            "reported the existing child failure honestly",
        )
    ]
    persisted = ReactCommandBoundary.from_record(
        await store.load_continuation("run-react")
    )
    assert persisted.pending_delegate is None
    assert persisted.outcomes[0] is not None
    assert persisted.outcomes[0].error["code"] == (
        "duplicate_failed_delegation"
    )
    assert persisted.completion_state["delegate_convergence_rejections"] == 1


@pytest.mark.asyncio
async def test_failed_child_objective_allows_materially_distinct_delegate(
    tmp_path,
):
    prior = _workflow_spawn_call(
        "Count every Python file in the repository",
        call_id="failed-count-spawn",
    )
    distinct = _workflow_spawn_call(
        "Generate a release-notes document from the changelog",
        call_id="distinct-release-spawn",
    )
    command = DelegateRun(
        run_id="run-react",
        command_id="delegate-distinct",
        child_request={"task": "generate release notes"},
        route_hint="workflow.durable_task",
        capability_subset=("read",),
        attachment_policy=AttachmentPolicy.ATTACHED,
        join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
    )

    class Collaborator(ScriptedCollaborator):
        async def prepare_control(self, request, prepared):
            del request
            assert prepared == distinct
            return command

    collaborator = Collaborator(
        [
            ReactControlBatch(
                "control-distinct",
                distinct,
                _context(distinct),
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": distinct.stable_call_id}],
                    },
                ),
            )
        ]
    )
    driver, _, _ = await _driver(tmp_path, collaborator)
    signature = driver._delegate_objective_signature(
        prior, route_hint="workflow.durable_task"
    )
    assert signature is not None
    signature.update(child_run_id="child-failed", command_id="delegate-failed")

    candidates = await _collect(
        driver.start(
            replace(
                _request(),
                completion_state={
                    "stop_reason": None,
                    "failed_delegate_objectives": [signature],
                },
            )
        )
    )

    assert candidates == [command]


@pytest.mark.asyncio
async def test_second_paraphrased_redelegation_exhausts_convergence_budget(
    tmp_path,
):
    prior = _workflow_spawn_call(
        "Count every Python file in the repository and report the total",
        call_id="failed-budget-spawn",
    )
    repeated = _workflow_spawn_call(
        "Report the total by counting every Python file in this repository",
        call_id="second-repeated-spawn",
    )
    collaborator = ScriptedCollaborator(
        [
            ReactControlBatch(
                "control-second-repeated",
                repeated,
                _context(repeated),
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": repeated.stable_call_id}],
                    },
                ),
            )
        ],
        resumes=[[ReactFinal("must not resume after budget exhaustion")]],
    )
    driver, _, _ = await _driver(tmp_path, collaborator)
    signature = driver._delegate_objective_signature(
        prior, route_hint="workflow.durable_task"
    )
    assert signature is not None
    signature.update(child_run_id="child-failed", command_id="delegate-failed")

    candidates = await _collect(
        driver.start(
            replace(
                _request(),
                completion_state={
                    "stop_reason": None,
                    "failed_delegate_objectives": [signature],
                    "delegate_convergence_rejections": 1,
                },
            )
        )
    )

    assert len(candidates) == 1
    assert candidates[0].kind == "terminal"
    assert candidates[0].status == "failed"
    assert "delegate_convergence_exhausted" in str(candidates[0].error)
    assert collaborator.resume_inputs == []


@pytest.mark.asyncio
async def test_failed_workflow_child_records_bounded_objective_signature(
    tmp_path,
):
    call = _workflow_spawn_call(
        "Count every Python file and report the total",
        call_id="record-failed-objective",
    )
    command = DelegateRun(
        run_id="run-react",
        command_id="delegate-record-failure",
        child_request={"task": "count Python files"},
        route_hint="workflow.durable_task",
        capability_subset=("read",),
        attachment_policy=AttachmentPolicy.ATTACHED,
        join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
    )

    class Collaborator(ScriptedCollaborator):
        async def prepare_control(self, request, prepared):
            del request
            assert prepared == call
            return command

    collaborator = Collaborator(
        [
            ReactControlBatch(
                "control-record-failure",
                call,
                _context(call),
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": call.stable_call_id}],
                    },
                ),
            )
        ],
        resumes=[[ReactFinal("child failed; no retry claimed")]],
    )
    driver, store, _ = await _driver(tmp_path, collaborator)
    assert await _collect(driver.start(_request())) == [command]
    coordinator, committed, accepted = await _schedule_child(store, command)
    await _collect(driver.signal(accepted))
    await store.finalize_child_and_enqueue_parent_signal(
        committed.operation_id,
        expected_version=1,
        terminal_status="failed",
        event=RunEventCandidate(
            event_key="workflow-child-failed",
            kind="run.failed",
            status="failed",
            driver_kind="react",
        ),
        value={"error": "provider_failure"},
    )
    terminal = (await _pending(coordinator, "run-react"))[0]

    candidates = await _collect(driver.signal(terminal))

    assert candidates[-1] == DriverTerminalCandidate(
        "run-react", "completed", "child failed; no retry claimed"
    )
    persisted = ReactCommandBoundary.from_record(
        await store.load_continuation("run-react")
    )
    history = persisted.completion_state["failed_delegate_objectives"]
    assert len(history) == 1
    assert history[0]["profile_key"] == "workflow.durable_task"
    assert history[0]["child_run_id"] == terminal.child_run_id
    assert "objective" not in history[0]
    assert history[0]["token_hashes"]


@pytest.mark.asyncio
async def test_late_child_terminal_preserves_authoritative_rejected_outcome(
    tmp_path, monkeypatch
):
    call = _call(0, durable=False)
    command = DelegateRun(
        run_id="run-react",
        command_id="delegate-control-child",
        child_request={"task": "historical child"},
        route_hint="workflow.durable_task",
        capability_subset=("read",),
        attachment_policy=AttachmentPolicy.ATTACHED,
        join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
    )

    class Collaborator(ScriptedCollaborator):
        async def prepare_control(self, request, prepared):
            del request, prepared
            return command

    collaborator = Collaborator(
        [
            ReactControlBatch(
                "control-parent",
                call,
                _context(call),
                (
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": call.stable_call_id}],
                    },
                ),
            )
        ],
        resumes=[[ReactFinal("continued after stale child")]],
    )
    driver, store, _ = await _driver(tmp_path, collaborator)

    async def keep_local_failure_set(boundary):
        return boundary

    monkeypatch.setattr(driver, "_persist_failure_set", keep_local_failure_set)
    assert await _collect(driver.start(_request())) == [command]
    coordinator, committed, accepted = await _schedule_child(store, command)
    await _collect(driver.signal(accepted))
    record = await store.load_continuation("run-react")
    assert record is not None
    boundary = ReactCommandBoundary.from_record(record)
    rejected = replace(
        boundary,
        outcomes=(
            NormalizedToolOutcome.failure(
                "replan_required", "same strategy was rejected"
            ),
        ),
        outcome_statuses=(OutcomeStatus.FAILED,),
        version=boundary.version + 1,
    )
    await driver._save_progress(rejected)
    await store.finalize_child_and_enqueue_parent_signal(
        committed.operation_id,
        expected_version=1,
        terminal_status="failed",
        event=RunEventCandidate(
            event_key="historical-child-failed",
            kind="run.failed",
            status="failed",
            driver_kind="react",
        ),
        value={"error": "provider_failure"},
    )
    terminal = (await _pending(coordinator, "run-react"))[0]

    candidates = await _collect(driver.signal(terminal))

    assert candidates[-1] == DriverTerminalCandidate(
        "run-react", "completed", "continued after stale child"
    )
    assert await store.list_pending_child_signals("run-react") == ()
    persisted = ReactCommandBoundary.from_record(
        await store.load_continuation("run-react")
    )
    assert persisted.pending_delegate is None
    assert persisted.outcomes[0] is not None
    assert persisted.outcomes[0].error["code"] == "replan_required"
    ignored = persisted.completion_state["ignored_child_terminals"]
    assert ignored[-1]["child_run_id"] == terminal.child_run_id
    assert ignored[-1]["reason"] == "authoritative_outcome_already_recorded"


@pytest.mark.asyncio
async def test_cancel_waits_for_collaborator_acknowledgement(tmp_path):
    collaborator = ScriptedCollaborator()
    driver, _, _ = await _driver(tmp_path, collaborator)

    candidates = await _collect(driver.cancel("run-react", "user_requested"))

    assert collaborator.cancelled == [("run-react", "user_requested")]
    assert candidates == [CancelAcknowledgedCandidate("run-react", "user_requested")]


@pytest.mark.asyncio
async def test_legacy_agent_loop_is_only_a_read_only_token_fallback_final_collaborator():
    from agent.agent_loop import (
        AssistantDeltaEvent,
        ContextCompactedEvent,
        FinalEvent,
        ProviderChainFallbackEvent,
    )

    @dataclass
    class FakeLoop:
        seen: tuple[Any, ...] | None = None

        async def _events(self):
            yield AssistantDeltaEvent(content="first")
            yield ProviderChainFallbackEvent(
                session_id="session-react",
                from_="primary",
                to="backup",
                reason="timeout",
            )
            yield ContextCompactedEvent(
                reduction=0.75,
                tokens_in=8000,
                tokens_out=2000,
                model="kimi-k3",
                source_event_id="context-compaction:session-react:run-react:2",
                based_on_sample_id=str(
                    getattr(self, "_context_usage_basis_sample_id", "")
                ),
                iteration=2,
                occurred_at=100.0,
            )
            yield FinalEvent(content="done")

        def run(self, messages, **kwargs):
            self.seen = (messages, kwargs)
            return self._events()

    loop = FakeLoop()
    collaborator = _bind_live(AgentLoopCollaborator(_factory(loop)), "run-react")[0]
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                request_payload={
                    "context_usage_basis_sample_id": "sample-before"
                },
            )
        )
    )

    assert emissions == [
        ReactToken("first"),
        ReactFallback("primary", "backup", "timeout"),
        ContextCompactedCandidate(
            "run-react",
            reduction=0.75,
            tokens_in=8000,
            tokens_out=2000,
            model="kimi-k3",
            source_event_id="context-compaction:session-react:run-react:2",
            based_on_sample_id="sample-before",
            iteration=2,
            occurred_at=100.0,
        ),
        ReactFinal("done"),
    ]
    assert loop.seen is not None
    assert loop.seen[1] == {
        "task_id": "run-react",
        "session_id": "session-react",
        "stream": True,
        "_scoped_evidence": UNKNOWN_EVIDENCE,
        "_external_tool_feedback": {"iteration": 2},
    }


@pytest.mark.asyncio
async def test_react_driver_forwards_context_compaction_candidate(tmp_path) -> None:
    compacted = ContextCompactedCandidate(
        "run-react",
        reduction=0.5,
        tokens_in=6000,
        tokens_out=3000,
        model="kimi-k3",
        source_event_id="context-compaction:session-react:run-react:3",
        iteration=3,
    )
    driver, _, _ = await _driver(
        tmp_path,
        ScriptedCollaborator(start=(compacted, ReactFinal("done"))),
    )

    candidates = await _collect(driver.start(_request()))

    assert candidates == [
        compacted,
        DriverTerminalCandidate("run-react", "completed", "done"),
    ]


@pytest.mark.asyncio
async def test_compaction_basis_survives_tool_boundary_restart(tmp_path) -> None:
    from deskpet.agent.context_usage import context_usage_sample_id

    source_event_id = "context-compaction:session-react:run-react:3"
    compacted = ContextCompactedCandidate(
        "run-react",
        reduction=0.5,
        tokens_in=6000,
        tokens_out=3000,
        model="kimi-k3",
        source_event_id=source_event_id,
        based_on_sample_id="sample-before",
        iteration=3,
    )
    call = _call(0, durable=False)
    batch = ReactToolBatch(
        "batch-after-compaction",
        (call,),
        (_context(call),),
        canonical_messages=(
            {"role": "system", "content": "compressed summary"},
            {"role": "assistant", "content": "using a tool"},
        ),
        iteration=3,
    )
    driver, store, _ = await _driver(
        tmp_path,
        ScriptedCollaborator(start=(compacted, batch)),
    )
    request = replace(
        _request(),
        request_payload={
            "context_usage_basis_sample_id": "sample-before"
        },
    )

    candidates = await _collect(driver.start(request))

    assert candidates[0] == compacted
    boundary = ReactCommandBoundary.from_record(
        await store.load_continuation("run-react")
    )
    expected = context_usage_sample_id("session-react", source_event_id)
    assert boundary.request_payload[
        "context_usage_basis_sample_id"
    ] == expected
    assert boundary.canonical_messages[0]["content"] == "compressed summary"

    class _RecoveredLoop:
        pass

    recovered_loop = _RecoveredLoop()
    collaborator = _bind_live(
        AgentLoopCollaborator(_factory(recovered_loop)),
        "run-react",
    )[0]
    await collaborator.prepare_recovery(boundary)
    assert recovered_loop._context_usage_basis_sample_id == expected


def test_runtime_projects_context_compaction_candidate() -> None:
    from deskpet.harness.runtime import DriverRuntime

    compacted = ContextCompactedCandidate(
        "run-react",
        reduction=0.6,
        tokens_in=5000,
        tokens_out=2000,
        model="kimi-k3",
        source_event_id="context-compaction:session-react:run-react:4",
        based_on_sample_id="sample-before",
        iteration=4,
        occurred_at=101.0,
    )

    event = DriverRuntime.event_candidate("react", compacted)

    assert event is not None
    assert event.kind == "context_compacted"
    assert event.event_key == "context-compaction:session-react:run-react:4"
    assert event.correlation == {
        "iteration": 4,
        "source_event_id": "context-compaction:session-react:run-react:4",
    }
    assert event.payload["model"] == "kimi-k3"
    assert event.payload["based_on_sample_id"] == "sample-before"
    assert event.payload["occurred_at"] == 101.0


@pytest.mark.asyncio
async def test_agent_loop_failure_keeps_structured_harness_layer() -> None:
    from agent.agent_loop import ErrorEvent

    class FailingLoop:
        async def _events(self):
            yield ErrorEvent(
                type="error",
                task_id="run-react",
                iteration=3,
                reason="tool_capability_scope_expired",
                detail="Context OS capability scope is missing or expired",
            )

        def run(self, _messages, **_kwargs):
            return self._events()

    collaborator = _bind_live(
        AgentLoopCollaborator(_factory(FailingLoop())), "run-react"
    )[0]
    emissions = await _collect(collaborator.start(_request()))

    assert emissions == [
        ReactFailure(
            "Context OS capability scope is missing or expired",
            error_code="tool_capability_scope_expired",
            source_layer="agent_loop",
        )
    ]
    driver = _react_driver(ScriptedCollaborator(start=emissions), SimpleNamespace())
    candidates = await _collect(driver.start(_request()))
    assert candidates == [
        DriverTerminalCandidate(
            "run-react",
            "failed",
            error="Context OS capability scope is missing or expired",
            correlation={
                "failure_layer": "agent_loop",
                "failure_code": "tool_capability_scope_expired",
            },
        )
    ]


@pytest.mark.asyncio
async def test_provider_turn_pins_and_rehydrates_context_os_scope_past_ttl(
    monkeypatch,
):
    from agent.agent_loop import FinalEvent
    from deskpet.tools import capabilities as capability_module

    monotonic = [10.0]
    monkeypatch.setattr(
        capability_module.time, "monotonic", lambda: monotonic[0]
    )

    scope_registry = ToolRegistry()
    scope_registry.register(
        "probe",
        "test",
        {
            "name": "probe",
            "description": "probe",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda _args, _task_id: {"ok": True},
    )
    eligibility = ToolEligibilityContext(
        "session-react", "request-run-react", "chat", mode="general"
    )
    prepared = ToolCapabilityResolver(scope_registry).resolve_draft(
        ToolExposureIntent(direct_selectors=("probe",)),
        eligibility=eligibility,
    ).finalize(scope_id="provider-turn-scope")
    context_os = dump_context_os_snapshot(prepared, eligibility)
    scope_store = ToolCapabilityScopeStore(ttl_seconds=5.0)
    # Model a durable resume whose old in-memory lease already expired.
    scope_store.open(prepared, eligibility)
    monotonic[0] = 16.0
    assert (
        scope_store.get(
            prepared.scope_id,
            session_id=eligibility.session_id,
            request_id=eligibility.request_id,
        )
        is None
    )

    observed_during_provider: list[bool] = []

    class SlowLoop:
        async def _events(self):
            monotonic[0] = 22.0
            observed_during_provider.append(
                scope_store.get(
                    prepared.scope_id,
                    session_id=eligibility.session_id,
                    request_id=eligibility.request_id,
                )
                is not None
            )
            yield FinalEvent(content="done")

        def run(self, _messages, **_kwargs):
            return self._events()

    request = replace(
        _request(),
        request_payload={"context_os": context_os},
        capability_snapshot={
            "tools": ["probe"],
            "prepared_tool_set_ref": fingerprint_json(context_os),
        },
    )
    collaborator = _bind_live(
        AgentLoopCollaborator(
            _factory(SlowLoop()),
            capability_scope_store=scope_store,
        ),
        "run-react",
    )[0]

    emissions = await _collect(collaborator.start(request))

    assert emissions == [ReactFinal("done")]
    assert observed_during_provider == [True]
    # Releasing the provider lease restarts the orphan TTL.
    monotonic[0] = 28.0
    assert (
        scope_store.get(
            prepared.scope_id,
            session_id=eligibility.session_id,
            request_id=eligibility.request_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_capability_snapshot_filters_agent_loop_schema_and_prepare() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        seen_tools = None

        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            self.seen_tools = tools
            return ChatResponse(
                content="",
                tool_calls=[ToolCall(id="denied-1", name="denied", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    class Tools:
        prepared = 0

        def schemas(self, enabled_toolsets=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for name in ("allowed", "denied")
            ]

        def prepare_call(self, *args, **kwargs):
            self.prepared += 1
            raise AssertionError("denied tool must not reach prepare")

    llm = LLM()
    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(llm, tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    request = replace(
        _request(), capability_snapshot={"tools": ["allowed"]}
    )

    emissions = await _collect(collaborator.start(request))

    assert [item["function"]["name"] for item in llm.seen_tools] == ["allowed"]
    assert tools.prepared == 0
    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert emissions[0].raw_failures[0]["error_code"] == "tool_not_exposed"
    assert emissions[0].raw_failures[0]["retriable"] is True
    assert emissions[0].raw_failures[0]["replan"] is True
    assert "capability_search" in emissions[0].raw_failures[0]["message"]


@pytest.mark.asyncio
async def test_provider_tool_batch_size_is_bounded_before_durable_admission() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"call-{index}",
                        name="allowed",
                        arguments={"index": index},
                    )
                    for index in range(33)
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            del enabled_toolsets
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "allowed",
                        "description": "allowed",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        def prepare_call(self, *args, **kwargs):
            raise AssertionError("oversized batch must fail before prepare")

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)),
        call_factory=tools,
    )
    _bind_live(collaborator, "run-react")

    emissions = await _collect(
        collaborator.start(
            replace(_request(), capability_snapshot={"tools": ["allowed"]})
        )
    )

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert emissions[0].provider_call_order == ("call-0",)
    assert (
        emissions[0].raw_failures[0]["error_code"]
        == "provider_tool_batch_too_large"
    )
    assert emissions[0].raw_failures[0]["source_kind"] == "tool_parse"
    assert emissions[0].raw_failures[0]["replan"] is True
    assert "workflow.durable_task" in emissions[0].raw_failures[0]["message"]


class _ReplayControlTools:
    def schemas(self, enabled_toolsets=None):
        del enabled_toolsets
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in ("workflow_spawn", "window_click")
        ]

    @staticmethod
    def dispatch_kind(name):
        return "delegate_control" if name == "workflow_spawn" else "sync"

    @staticmethod
    def prepared_execution_policy(_call):
        return False, False

    def prepare_call(
        self, name, arguments, session_id, call_id, *, execution_context
    ):
        del session_id, execution_context
        return PreparedToolCall.prepare(
            tool_name=name,
            stable_call_id=call_id,
            final_params=arguments,
            tool_spec_version="v1",
            schema_hash="schema-v1",
            permission_policy_version="policy-v1",
            effect_type="opaque_manual",
        )


@pytest.mark.asyncio
async def test_replayed_exclusive_workflow_spawn_is_one_control_command() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    arguments = {
        "profile_key": "workflow.presentation",
        "catalog_generation": 1,
        "workspace_ref": "workspace://presentation-test",
        "objective": "Generate an eight-slide editable presentation.",
    }
    raw = json.dumps(
        arguments,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="delegating",
                tool_calls=[
                    *[
                        ToolCall(
                            id=(
                                "spawn-first"
                                if index == 0
                                else f"spawn-replay-{index}"
                            ),
                            name="workflow_spawn",
                            arguments=(
                                arguments
                                if index % 2 == 0
                                else dict(reversed(tuple(arguments.items())))
                            ),
                        )
                        for index in range(18)
                    ],
                    ToolCall(
                        id="spawn-truncated",
                        name="workflow_spawn",
                        arguments={},
                        args_parse_error="Unterminated string",
                        args_raw=raw[:80],
                    ),
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="sf-glm-5.2",
            )

    tools = _ReplayControlTools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                capability_snapshot={
                    "tools": ["workflow_spawn", "window_click"]
                },
            )
        )
    )

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactControlBatch)
    assert emissions[0].call.stable_call_id == "spawn-first"
    assert emissions[0].call.final_params == arguments
    assistant = next(
        item
        for item in reversed(emissions[0].canonical_messages)
        if item.get("role") == "assistant"
    )
    assert [item["id"] for item in assistant["tool_calls"]] == ["spawn-first"]


def test_replayed_exclusive_control_selection_is_recovery_stable() -> None:
    from agent.agent_loop import ToolBatchEvent
    from llm.types import ToolCall

    arguments = {
        "profile_key": "workflow.presentation",
        "catalog_generation": 1,
        "objective": "Generate the presentation.",
    }
    raw = json.dumps(arguments, ensure_ascii=False)
    calls = (
        ToolCall(
            id="spawn-first",
            name="workflow_spawn",
            arguments=arguments,
        ),
        ToolCall(
            id="spawn-second",
            name="workflow_spawn",
            arguments=dict(reversed(tuple(arguments.items()))),
        ),
        ToolCall(
            id="spawn-truncated",
            name="workflow_spawn",
            arguments={},
            args_parse_error="Unterminated string",
            args_raw=raw[:40],
        ),
    )
    canonical = (
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": (
                            call.args_raw
                            if call.args_parse_error
                            else json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                            )
                        ),
                    },
                }
                for call in calls
            ],
        },
    )
    original = ToolBatchEvent(
        task_id="task",
        iteration=3,
        tool_calls=calls,
        canonical_messages=canonical,
    )
    collaborator = AgentLoopCollaborator(
        lambda _request: None,
        call_factory=_ReplayControlTools(),
    )

    first = collaborator._coalesce_replayed_exclusive_control(original)
    replay = collaborator._coalesce_replayed_exclusive_control(original)

    assert first == replay
    assert [call.id for call in first.tool_calls] == ["spawn-first"]
    assert first.canonical_messages[0]["tool_calls"][0]["id"] == "spawn-first"
    assert original.tool_calls == calls
    assert len(original.canonical_messages[0]["tool_calls"]) == 3


@pytest.mark.asyncio
async def test_identical_ordinary_tool_calls_are_not_coalesced() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="click twice",
                tool_calls=[
                    ToolCall(
                        id=f"click-{index}",
                        name="window_click",
                        arguments={"x": 10, "y": 20},
                    )
                    for index in range(2)
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    tools = _ReplayControlTools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                capability_snapshot={
                    "tools": ["workflow_spawn", "window_click"]
                },
            )
        )
    )

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert [call.stable_call_id for call in emissions[0].calls] == [
        "click-0",
        "click-1",
    ]


@pytest.mark.asyncio
async def test_control_and_ordinary_tool_mixture_remains_rejected() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="spawn-a",
                        name="workflow_spawn",
                        arguments={"objective": "A"},
                    ),
                    ToolCall(
                        id="click-a",
                        name="window_click",
                        arguments={"x": 10, "y": 20},
                    ),
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    tools = _ReplayControlTools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                capability_snapshot={
                    "tools": ["workflow_spawn", "window_click"]
                },
            )
        )
    )

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert len(emissions[0].raw_failures) == 2
    assert {
        item["error_code"] for item in emissions[0].raw_failures
    } == {"control_batch_not_exclusive"}


@pytest.mark.asyncio
async def test_length_finished_control_call_never_reaches_preparation() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="truncated",
                tool_calls=[
                    ToolCall(
                        id="spawn-truncated-turn",
                        name="workflow_spawn",
                        arguments={"objective": "build slides"},
                    )
                ],
                stop_reason="length",
                usage=ChatUsage(input_tokens=1, output_tokens=8192),
                model="sf-glm-5.2",
            )

    class Tools(_ReplayControlTools):
        def __init__(self):
            self.prepared = 0

        def prepare_call(self, *args, **kwargs):
            self.prepared += 1
            return super().prepare_call(*args, **kwargs)

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                capability_snapshot={"tools": ["workflow_spawn"]},
            )
        )
    )

    assert tools.prepared == 0
    assert all(
        not isinstance(item, (ReactControlBatch, ReactToolBatch))
        for item in emissions
    )
    assert isinstance(emissions[-1], ReactFinal)


@pytest.mark.asyncio
async def test_distinct_exclusive_control_calls_remain_rejected() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="spawn-a",
                        name="workflow_spawn",
                        arguments={"objective": "A"},
                    ),
                    ToolCall(
                        id="spawn-b",
                        name="workflow_spawn",
                        arguments={"objective": "B"},
                    ),
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    tools = _ReplayControlTools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                capability_snapshot={"tools": ["workflow_spawn"]},
            )
        )
    )

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert {
        item["error_code"] for item in emissions[0].raw_failures
    } == {"control_batch_not_exclusive"}


@pytest.mark.asyncio
async def test_unrelated_malformed_control_tail_prevents_coalescing() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="spawn-valid",
                        name="workflow_spawn",
                        arguments={"objective": "build slides"},
                    ),
                    ToolCall(
                        id="spawn-broken",
                        name="workflow_spawn",
                        arguments={},
                        args_parse_error="Unterminated string",
                        args_raw='{"objective": "different and unfinished',
                    ),
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    tools = _ReplayControlTools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                capability_snapshot={"tools": ["workflow_spawn"]},
            )
        )
    )

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert {
        item["error_code"] for item in emissions[0].raw_failures
    } == {
        "tool_call_args_malformed_json",
        "control_batch_not_exclusive",
    }


@pytest.mark.asyncio
async def test_oversized_batch_preserves_one_valid_workflow_spawn_intent() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="delegating",
                tool_calls=[
                    ToolCall(
                        id="spawn-1",
                        name="workflow_spawn",
                        arguments={
                            "profile_key": "workflow.durable_task",
                            "objective": "build the project",
                        },
                    ),
                    *[
                        ToolCall(
                            id=f"search-{index}",
                            name="capability_search",
                            arguments={"query": f"noise-{index}"},
                        )
                        for index in range(32)
                    ],
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            del enabled_toolsets
            return [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for name in ("workflow_spawn", "capability_search")
            ]

        @staticmethod
        def dispatch_kind(name):
            return "delegate_control" if name == "workflow_spawn" else "sync"

        @staticmethod
        def prepared_execution_policy(_call):
            return False, False

        def prepare_call(
            self, name, arguments, session_id, call_id, *, execution_context
        ):
            del session_id, execution_context
            return PreparedToolCall.prepare(
                tool_name=name,
                stable_call_id=call_id,
                final_params=arguments,
                tool_spec_version="v1",
                schema_hash="schema-v1",
                permission_policy_version="policy-v1",
                effect_type="opaque_manual",
            )

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")
    emissions = await _collect(
        collaborator.start(
            replace(
                _request(),
                capability_snapshot={
                    "tools": ["workflow_spawn", "capability_search"]
                },
            )
        )
    )

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactControlBatch)
    assert emissions[0].call.tool_name == "workflow_spawn"
    assistant = next(
        item
        for item in reversed(emissions[0].canonical_messages)
        if item.get("role") == "assistant"
    )
    assert [item["id"] for item in assistant["tool_calls"]] == ["spawn-1"]


@pytest.mark.asyncio
async def test_oversized_provider_batch_releases_iterator_before_replan() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"call-{index}",
                        name="allowed",
                        arguments={"index": index},
                    )
                    for index in range(33)
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            del enabled_toolsets
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "allowed",
                        "description": "allowed",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        def prepare_call(self, *args, **kwargs):
            raise AssertionError("oversized batch must fail before prepare")

    tools = Tools()
    collaborator, live = _bind_live(
        AgentLoopCollaborator(
            _factory(AgentLoop(LLM(), tools)),
            call_factory=tools,
        ),
        "run-react",
    )
    stream = collaborator.start(
        replace(_request(), capability_snapshot={"tools": ["allowed"]})
    )

    emission = await anext(stream)

    assert isinstance(emission, ReactToolBatch)
    assert emission.raw_failures[0]["replan"] is True
    assert live.get("run-react").driver_iterator is None
    await stream.aclose()


@pytest.mark.asyncio
async def test_oversized_provider_batch_closes_old_agent_loop_before_replan() -> None:
    from agent.agent_loop import ToolBatchEvent
    from llm.types import ToolCall

    class CloseAwareLoop:
        def __init__(self) -> None:
            self.closed = False

        async def _events(self):
            try:
                yield ToolBatchEvent(
                    task_id="run-react",
                    iteration=1,
                    tool_calls=tuple(
                        ToolCall(
                            id=f"call-{index}",
                            name="allowed",
                            arguments={"index": index},
                        )
                        for index in range(33)
                    ),
                    canonical_messages=(
                        {"role": "user", "content": "build it"},
                    ),
                    feedback_state={},
                )
            finally:
                self.closed = True

        def run(self, messages, **kwargs):
            del messages, kwargs
            return self._events()

    class Tools:
        def prepare_call(self, *args, **kwargs):
            raise AssertionError("oversized batch must fail before prepare")

    loop = CloseAwareLoop()
    collaborator, live = _bind_live(
        AgentLoopCollaborator(_factory(loop), call_factory=Tools()),
        "run-react",
    )
    stream = collaborator.start(
        replace(_request(), capability_snapshot={"tools": ["allowed"]})
    )

    emission = await anext(stream)

    assert isinstance(emission, ReactToolBatch)
    assert emission.raw_failures[0]["replan"] is True
    assert loop.closed is True
    assert live.get("run-react").driver_iterator is None
    await stream.aclose()


@pytest.mark.asyncio
async def test_missing_provider_tool_name_becomes_durable_raw_failure() -> None:
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            del messages, tools, kwargs
            return ChatResponse(
                content="",
                tool_calls=[ToolCall(id="missing-name", name="", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            del enabled_toolsets
            return []

        def prepare_call(self, *args, **kwargs):
            raise AssertionError("missing tool name must not reach prepare")

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)),
        call_factory=tools,
    )
    _bind_live(collaborator, "run-react")

    emissions = await _collect(collaborator.start(_request()))

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert emissions[0].raw_failures[0]["error_code"] == "tool_call_name_missing"
    assert emissions[0].raw_failures[0]["raw_tool_name"] == "<missing-tool-name>"


@pytest.mark.asyncio
async def test_react_boundary_roundtrip_and_real_resume_retain_capability_snapshot() -> None:
    boundary = ReactCommandBoundary(
        run_id="run-react",
        session_id="session-react",
        command_id="command-1",
        command_kind="execute_tools",
        canonical_messages=({"role": "user", "content": "do it"},),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=None,
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        provider_state={},
        iteration=0,
        completion_state={},
        capability_snapshot={"tools": ["read_file"]},
    )
    restored = ReactCommandBoundary.from_record(
        SimpleNamespace(run_id="run-react", payload=boundary.to_payload(), version=3)
    )

    assert dict(restored.capability_snapshot) == {"tools": ["read_file"]}
    resumed = restored.to_start()
    assert dict(resumed.capability_snapshot) == {"tools": ["read_file"]}

    class Loop:
        def __init__(self):
            self.kwargs = None

        async def _events(self):
            from agent.agent_loop import FinalEvent
            yield FinalEvent(content="done")

        def run(self, messages, **kwargs):
            self.kwargs = kwargs
            return self._events()

    loop = Loop()
    collaborator = _bind_live(AgentLoopCollaborator(_factory(loop)), "run-react")[0]
    await _collect(collaborator.resume(restored, {}))
    assert loop.kwargs["tool_names_filter"] == ["read_file"]


def test_react_boundary_thaws_nested_provider_mapping_proxies() -> None:
    nested_arguments = MappingProxyType(
        {
            "schedule": MappingProxyType(
                {
                    "kind": "weekly",
                    "weekday": 5,
                    "local_time": "15:00",
                    "timezone": "Asia/Shanghai",
                }
            )
        }
    )
    boundary = ReactCommandBoundary(
        run_id="run-react",
        session_id="session-react",
        command_id="command-reminder",
        command_kind="execute_tools",
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": (
                    MappingProxyType(
                        {
                            "id": "call-reminder",
                            "name": "reminder_create",
                            "arguments": nested_arguments,
                        }
                    ),
                ),
            },
        ),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=None,
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        provider_state={"nested": nested_arguments},
        iteration=0,
        completion_state={"nested": nested_arguments},
        capability_snapshot={},
        request_payload={"nested": nested_arguments},
    )

    payload = boundary.to_payload()

    assert payload["canonical_messages"][0]["tool_calls"][0]["arguments"][
        "schedule"
    ]["weekday"] == 5
    assert payload["provider_state"]["nested"]["schedule"]["local_time"] == "15:00"
    assert payload["completion_state"]["nested"]["schedule"]["kind"] == "weekly"
    assert payload["request_payload"]["nested"]["schedule"]["timezone"] == (
        "Asia/Shanghai"
    )


@pytest.mark.asyncio
async def test_legacy_collaborator_fails_closed_before_legacy_tool_dispatch_can_leak():
    from agent.agent_loop import ToolCallEvent

    class FakeLoop:
        async def _events(self):
            yield ToolCallEvent()

        def run(self, messages, **kwargs):
            return self._events()

    collaborator = _bind_live(AgentLoopCollaborator(_factory(FakeLoop())), "run-react")[0]
    with pytest.raises(AgentLoopToolInterceptionError):
        await _collect(collaborator.start(_request()))


@pytest.mark.asyncio
async def test_agent_loop_factory_is_scoped_to_the_single_live_run_index():
    created = []

    class Loop:
        def __init__(self, run_id):
            self.run_id = run_id

        async def _events(self):
            from agent.agent_loop import FinalEvent

            yield FinalEvent(content=self.run_id)

        def run(self, _messages, **_kwargs):
            return self._events()

    async def factory(request):
        created.append(request.run_id)
        return Loop(request.run_id)

    collaborator, live = _bind_live(
        AgentLoopCollaborator(loop_factory=factory), "run-a", "run-b"
    )
    first = await _collect(collaborator.start(_request("run-a")))
    again = await _collect(collaborator.start(_request("run-a")))
    second = await _collect(collaborator.start(_request("run-b")))

    assert [item.content for item in (*first, *again, *second)] == [
        "run-a", "run-a", "run-b"
    ]
    assert created == ["run-a", "run-b"]
    assert live.get("run-a").driver_runtime is not live.get("run-b").driver_runtime


@pytest.mark.asyncio
async def test_agent_loop_binding_rehydrates_host_run_options_without_overriding_fences():
    class Loop:
        def __init__(self):
            self.kwargs = None

        async def _events(self):
            from agent.agent_loop import FinalEvent

            yield FinalEvent(content="done")

        def run(self, _messages, **kwargs):
            self.kwargs = kwargs
            return self._events()

    loop = Loop()

    def factory(_request):
        return (
            loop,
            {
                "provider_chain": ["primary", "fallback"],
                "loop_user_request": "original request",
                "task_id": "forged-task",
                "session_id": "forged-session",
                "stream": False,
            },
        )

    collaborator = _bind_live(
        AgentLoopCollaborator(loop_factory=factory), "run-react"
    )[0]
    await _collect(collaborator.start(_request()))

    assert loop.kwargs["provider_chain"] == ["primary", "fallback"]
    assert loop.kwargs["loop_user_request"] == "original request"
    assert loop.kwargs["task_id"] == "run-react"
    assert loop.kwargs["session_id"] == "session-react"
    assert loop.kwargs["stream"] is True


@pytest.mark.asyncio
async def test_external_agent_loop_emits_prepared_batch_before_any_dispatch():
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            return ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(id="call-1", name="probe", arguments={"value": 7})
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    class Tools:
        def __init__(self):
            self.executed = 0

        def schemas(self, enabled_toolsets=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "probe",
                        "description": "probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        async def execute_tool(self, *args, **kwargs):
            self.executed += 1
            return {"ok": True}

        def prepare_call(
            self, tool_name, raw_args, session_id, stable_call_id, *,
            execution_context
        ):
            return prepared_call(
                tool_name=tool_name,
                model_args=raw_args,
                call_id=stable_call_id,
                effect_id=execution_context.effect_id,
                capability_hash=execution_context.capability_hash,
                scope_hash=execution_context.scope_hash,
            )

    tools = Tools()
    loop = AgentLoop(LLM(), tools)
    collaborator = AgentLoopCollaborator(_factory(loop), call_factory=tools)
    _bind_live(collaborator, "run-react")
    context = RunContext(
        session_id="session-react",
        root_run_id="run-react",
        parent_run_id=None,
        request_id="request-react",
        turn_id="turn-react",
        venue="text",
        workspace={"scope_hash": SCOPE_HASH},
        capability_hash=CAPABILITY_HASH,
        provider_plan={},
        trace_id="trace-react",
        principal_id="principal-react",
    )
    request = DriverStart(
        run_id="run-react",
        session_id="session-react",
        canonical_messages=({"role": "user", "content": "use probe"},),
        run_context=context,
        profile_key="react.default",
    )
    scope_registry = ToolRegistry()
    scope_registry.register(
        "probe",
        "test",
        {
            "name": "probe",
            "description": "probe",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda _args, _task_id: {"ok": True},
    )
    eligibility = ToolEligibilityContext(
        "session-react", "request-react", "chat", mode="general"
    )
    prepared_scope = ToolCapabilityResolver(scope_registry).resolve_draft(
        ToolExposureIntent(direct_selectors=("probe",)),
        eligibility=eligibility,
    ).finalize(scope_id="context-os-scope")
    request = replace(
        request,
        request_payload={
            "context_os": dump_context_os_snapshot(
                prepared_scope, eligibility
            )
        },
        capability_snapshot={"tools": ["probe"]},
    )

    emissions = await _collect(collaborator.start(request))

    assert tools.executed == 0
    assert len(emissions) == 1
    batch = emissions[0]
    assert isinstance(batch, ReactToolBatch)
    assert [call.tool_name for call in batch.calls] == ["probe"]
    assert batch.contexts[0].command_id == batch.command_id
    assert batch.contexts[0].scope_id == "context-os-scope"
    assert batch.canonical_messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_external_tool_boundary_releases_provider_iterator_before_yield():
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            return ChatResponse(
                content="", stop_reason="tool_use",
                tool_calls=[ToolCall(id="call-read", name="probe", arguments={})],
                usage=ChatUsage(input_tokens=1, output_tokens=1), model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            return [{"type": "function", "function": {
                "name": "probe", "description": "probe",
                "parameters": {"type": "object", "properties": {}},
            }}]

        def prepare_call(
            self, tool_name, raw_args, session_id, stable_call_id, *,
            execution_context,
        ):
            return prepared_call(
                tool_name=tool_name, model_args=raw_args,
                call_id=stable_call_id, effect_id=execution_context.effect_id,
                capability_hash=execution_context.capability_hash,
                scope_hash=execution_context.scope_hash,
            )

    tools = Tools()
    collaborator, live = _bind_live(
        AgentLoopCollaborator(
            _factory(AgentLoop(LLM(), tools)), call_factory=tools,
        ),
        "run-react",
    )
    request = _request()
    context = replace(request.run_context, workspace={"scope_hash": SCOPE_HASH})
    request = replace(
        request, run_context=context,
        run_spec=replace(request.run_spec, context=context),
    )
    stream = collaborator.start(request)

    emission = await anext(stream)

    assert isinstance(emission, ReactToolBatch)
    assert live.get("run-react").driver_iterator is None
    await stream.aclose()


@pytest.mark.asyncio
async def test_external_agent_loop_uses_real_registry_prepare_contract():
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            return ChatResponse(
                content="", stop_reason="tool_use",
                tool_calls=[ToolCall(id="call-real", name="probe", arguments={})],
                usage=ChatUsage(input_tokens=1, output_tokens=1), model="fixture",
            )

    registry = ToolRegistry()
    registry.register(
        "probe", "test",
        {"name": "probe", "description": "probe",
         "parameters": {"type": "object", "properties": {}}},
        lambda args, task_id: "ok",
    )
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), registry)), call_factory=registry
    )
    _bind_live(collaborator, "run-react")

    batch = (await _collect(collaborator.start(_request())))[0]

    assert isinstance(batch, ReactToolBatch)
    assert batch.calls[0].tool_name == "probe"
    assert batch.calls[0].stable_call_id == "call-real"
    restored = ReactCommandBoundary.from_record(
        SimpleNamespace(
            run_id="run-react",
            payload=ReactCommandBoundary(
                run_id="run-react",
                session_id="session-react",
                command_id=batch.command_id,
                command_kind="execute_tools",
                canonical_messages=batch.canonical_messages,
                session_projection_cursor=0,
                prepared_context_ref=None,
                tool_set_snapshot_ref=None,
                pending_calls=batch.calls,
                tool_contexts=batch.contexts,
                outcomes=(None,),
                provider_state={},
                iteration=0,
                completion_state={},
            ).to_payload(),
            version=1,
        )
    )
    assert restored.tool_contexts[0].command_id == batch.command_id


@pytest.mark.asyncio
async def test_malformed_tool_arguments_fail_before_registry_prepare():
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            return ChatResponse(
                content="", stop_reason="tool_use",
                tool_calls=[ToolCall(
                    id="bad", name="probe", arguments={}, args_raw="{",
                    args_parse_error="unterminated object",
                )],
                usage=ChatUsage(input_tokens=1, output_tokens=1), model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            return [{"type": "function", "function": {
                "name": "probe", "parameters": {"type": "object"}
            }}]

        def prepare_call(self, *args, **kwargs):
            raise AssertionError("malformed args must not be prepared")

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")

    emissions = await _collect(collaborator.start(_request()))

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert (
        emissions[0].raw_failures[0]["error_code"]
        == "tool_call_args_malformed_json"
    )
    assert "malformed tool arguments" in emissions[0].raw_failures[0]["message"]


@pytest.mark.asyncio
async def test_authorization_scope_missing_is_structured_tool_failure():
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            return ChatResponse(
                content="",
                stop_reason="tool_use",
                tool_calls=[
                    ToolCall(id="scope-missing", name="probe", arguments={})
                ],
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "probe",
                        "parameters": {"type": "object"},
                    },
                }
            ]

        def dispatch_kind(self, _name):
            return "handler"

        def prepare_call(
            self, name, arguments, session_id, call_id, **kwargs
        ):
            return PreparedToolCall.prepare(
                tool_name=name,
                    stable_call_id=call_id,
                    final_params=arguments,
                    tool_spec_version="v1",
                    schema_hash="a" * 64,
                permission_policy_version="v1",
                effect_type="opaque_manual",
                resource_selectors=(),
            )

        def prepared_execution_policy(self, _prepared):
            return True, True

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools
    )
    _bind_live(collaborator, "run-react")

    emissions = await _collect(collaborator.start(_request()))

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    failure = emissions[0].raw_failures[0]
    assert failure["error_code"] == "authorization_scope_missing"
    assert failure["source_kind"] == "tool_authorization"
    assert failure["retriable"] is True
    assert failure["replan"] is True


@pytest.mark.asyncio
async def test_delegation_is_validated_before_delegate_factory():
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            return ChatResponse(
                content="", stop_reason="tool_use",
                tool_calls=[ToolCall(
                    id="bad-delegate", name="agent", arguments={}, args_raw="{",
                    args_parse_error="unterminated object",
                )],
                usage=ChatUsage(input_tokens=1, output_tokens=1), model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            return [{"type": "function", "function": {
                "name": "agent", "parameters": {"type": "object"}
            }}]

    def delegate_factory(*_args, **_kwargs):
        raise AssertionError("malformed delegation must not reach the factory")

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools,
        delegation_factory=delegate_factory,
    )
    _bind_live(collaborator, "run-react")

    emissions = await _collect(collaborator.start(_request()))

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert (
        emissions[0].raw_failures[0]["error_code"]
        == "tool_call_args_malformed_json"
    )


@pytest.mark.asyncio
async def test_delegation_capability_is_checked_before_delegate_factory():
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            return ChatResponse(
                content="", stop_reason="tool_use",
                tool_calls=[ToolCall(id="outside", name="agent", arguments={})],
                usage=ChatUsage(input_tokens=1, output_tokens=1), model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            return [{"type": "function", "function": {
                "name": "agent", "parameters": {"type": "object"}
            }}]

    def delegate_factory(*_args, **_kwargs):
        raise AssertionError("out-of-scope delegation must not reach the factory")

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)), call_factory=tools,
        delegation_factory=delegate_factory,
    )
    _bind_live(collaborator, "run-react")
    request = replace(_request(), capability_snapshot={"tools": ["probe"]})

    emissions = await _collect(collaborator.start(request))

    assert len(emissions) == 1
    assert isinstance(emissions[0], ReactToolBatch)
    assert emissions[0].calls == ()
    assert emissions[0].raw_failures[0]["error_code"] == "tool_not_exposed"


@pytest.mark.asyncio
async def test_raw_admission_failure_is_durable_and_replans_in_same_root(
    tmp_path,
) -> None:
    raw = AgentLoopCollaborator._raw_failure(
        run_id="run-react",
        tool_call=SimpleNamespace(
            id="bad-json",
            name="probe",
            arguments={},
            args_raw="{",
            args_parse_error="unterminated object",
        ),
        call_order=0,
        source_kind="tool_parse",
        error_code="tool_call_args_malformed_json",
        message="malformed tool arguments for probe: unterminated object",
    )
    batch = ReactToolBatch(
        command_id="raw-batch",
        calls=(),
        contexts=(),
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "bad-json",
                        "type": "function",
                        "function": {"name": "probe", "arguments": "{"},
                    }
                ],
            },
        ),
        iteration=3,
        raw_failures=(raw,),
        provider_call_order=("bad-json",),
    )
    class DurableBeforeResumeCollaborator(ScriptedCollaborator):
        store = None
        durable_before_resume = False

        async def resume(self, boundary, response):
            failure_set_id = str(
                boundary.completion_state["latest_failure_set_id"]
            )
            failure_set = await self.store.get_attempt_failure_set(
                failure_set_id
            )
            reports = await self.store.list_task_failure_reports(
                str(boundary.completion_state["latest_attempt_id"])
            )
            self.durable_before_resume = (
                failure_set is not None
                and tuple(failure_set.report_refs)
                == tuple(report.report_ref for report in reports)
            )
            async for emission in super().resume(boundary, response):
                yield emission

    collaborator = DurableBeforeResumeCollaborator(
        start=(batch,),
        resumes=((ReactFinal("replanned and completed"),),),
    )
    driver, store, _ = await _driver(tmp_path, collaborator)
    collaborator.store = store

    events = await _collect(driver.start(_request()))

    assert len(events) == 1
    assert events[0].kind == "terminal"
    assert events[0].status == "completed"
    assert len(collaborator.resume_inputs) == 1
    assert collaborator.durable_before_resume is True
    resumed = collaborator.resume_inputs[0][0]
    assert resumed.run_id == "run-react"
    assert resumed.completion_state["attempt_status"] == "failed"
    assert resumed.completion_state["latest_failure_set_id"]
    tool_message = resumed.canonical_messages[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "bad-json"
    payload = json.loads(str(tool_message["content"]))
    assert (
        payload["failure_report"]["source_kind"]
        == "tool_parse"
    )
    assert payload["failure_report"]["plan_version"] == 1

    record = await store.load_continuation("run-react")
    persisted = ReactCommandBoundary.from_record(record)
    assert persisted.raw_failures[0]["failure_report_ref"]
    assert persisted.completion_state["model_backfilled"] is True
    attempt_id = str(persisted.completion_state["latest_attempt_id"])
    attempt = await store.get_attempt(attempt_id)
    failure_set = await store.get_attempt_failure_set(
        str(persisted.completion_state["latest_failure_set_id"])
    )
    reports = await store.list_task_failure_reports(attempt_id)
    durable_calls = await store.list_provider_action_calls("raw-batch")
    assert attempt is not None and attempt.status.value == "failed"
    assert failure_set is not None
    assert tuple(failure_set.report_refs) == tuple(
        report.report_ref for report in reports
    )
    assert len(durable_calls) == 1
    assert durable_calls[0].provider_call_id == "bad-json"
    assert durable_calls[0].admission_state.value == "rejected"
    assert durable_calls[0].terminal_outcome_ref

    # A restart re-admits the persisted boundary before resuming the model.
    # The failed attempt must retain its original (None) trigger instead of
    # being rewritten to point at its own newly-created failure set.
    await driver._admit_provider_boundary(persisted)


@pytest.mark.asyncio
async def test_replan_appends_durable_plan_and_links_the_next_attempt(
    tmp_path,
) -> None:
    raw = AgentLoopCollaborator._raw_failure(
        run_id="run-react",
        tool_call=SimpleNamespace(
            id="bad-json",
            name="probe",
            arguments={},
            args_raw="{",
            args_parse_error="unterminated object",
        ),
        call_order=0,
        source_kind="tool_parse",
        error_code="tool_call_args_malformed_json",
        message="malformed tool arguments for probe: unterminated object",
    )
    failed_batch = ReactToolBatch(
        command_id="failed-batch",
        calls=(),
        contexts=(),
        raw_failures=(raw,),
        provider_call_order=("bad-json",),
    )
    retry_call = _call(1, durable=False)
    retry_batch = ReactToolBatch(
        command_id="retry-batch",
        calls=(retry_call,),
        contexts=(_context(retry_call),),
    )
    collaborator = ScriptedCollaborator(
        start=(failed_batch,),
        resumes=((retry_batch,),),
    )
    driver, store, path = await _driver(tmp_path, collaborator)

    events = await _collect(driver.start(_request()))

    assert events == [
        ExecuteTools(
            "run-react",
            "retry-batch",
            (retry_call,),
            (_context(retry_call),),
            (0,),
            effectful=(False,),
        )
    ]
    persisted = ReactCommandBoundary.from_record(
        await store.load_continuation("run-react")
    )
    current = dict(persisted.completion_state["current_attempt"])
    next_attempt = await store.get_attempt(str(current["attempt_id"]))
    assert next_attempt is not None
    assert next_attempt.plan_version == 2
    assert next_attempt.trigger_failure_set_id
    failure_set = await store.get_attempt_failure_set(
        str(next_attempt.trigger_failure_set_id)
    )
    assert failure_set is not None
    assert next_attempt.supersedes_attempt_id == failure_set.failed_attempt_id
    async with aiosqlite.connect(path) as db:
        plans = await (
            await db.execute(
                """SELECT plan_version,trigger_failure_set_id
                FROM execution_plan_versions
                WHERE root_run_id='run-react'
                ORDER BY plan_version"""
            )
        ).fetchall()
    assert plans == [
        (1, None),
        (2, next_attempt.trigger_failure_set_id),
    ]


def test_mixed_batch_backfill_preserves_original_provider_call_order() -> None:
    call = _call(0, durable=False)
    raw = AgentLoopCollaborator._raw_failure(
        run_id="run-react",
        tool_call=SimpleNamespace(
            id="bad-middle",
            name="missing_tool",
            arguments={"value": 1},
            args_raw=None,
            args_parse_error=None,
        ),
        call_order=0,
        source_kind="tool_unknown",
        error_code="unknown_tool",
        message="unknown tool: missing_tool",
    )
    request = _request()
    driver = ReActDriver(object(), object(), PolicyRegistry())
    boundary = driver._boundary_for_batch(
        request,
        ReactToolBatch(
            command_id="mixed-batch",
            calls=(call,),
            contexts=(_context(call),),
            canonical_messages=(
                {"role": "assistant", "tool_calls": []},
            ),
            iteration=3,
            raw_failures=(raw,),
            provider_call_order=("bad-middle", call.stable_call_id),
        ),
    ).with_outcomes(
        {0: _outcome(call)},
        {0: OutcomeStatus.SUCCEEDED},
    )

    staged = ReActDriver._stage_failure_reports(boundary)
    tool_messages = ReActDriver._tool_messages(staged)[-2:]

    assert [item["tool_call_id"] for item in tool_messages] == [
        "bad-middle",
        call.stable_call_id,
    ]
    assert (
        json.loads(str(tool_messages[0]["content"]))["failure_report"][
            "source_kind"
        ]
        == "tool_unknown"
    )
    assert (
        json.loads(str(tool_messages[1]["content"]))["status"]
        == OutcomeStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_durable_tool_resume_can_prepare_and_persist_a_second_tool(tmp_path):
    from agent.agent_loop import AgentLoop
    from llm.types import ChatResponse, ChatUsage, ToolCall

    class LLM:
        def __init__(self):
            self.calls = iter(("tool-a", "tool-b"))

        async def chat_with_fallback(self, messages, tools=None, **kwargs):
            name = next(self.calls)
            return ChatResponse(
                content="", tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments={})],
                stop_reason="tool_use", usage=ChatUsage(input_tokens=1, output_tokens=1), model="fixture",
            )

    class Tools:
        def schemas(self, enabled_toolsets=None):
            return [{"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}} for name in ("tool-a", "tool-b")]

        def prepare_call(self, tool_name, raw_args, session_id, stable_call_id, *, execution_context):
            return prepared_call(tool_name=tool_name, model_args=raw_args, call_id=stable_call_id, effect_id=execution_context.effect_id, capability_hash=execution_context.capability_hash, scope_hash=execution_context.scope_hash, recoverable_effect=True)

    tools = Tools()
    collaborator = AgentLoopCollaborator(
        _factory(AgentLoop(LLM(), tools)),
        call_factory=tools,
    )
    driver, store, _ = await _driver(tmp_path, collaborator)
    request = _request()
    context = replace(request.run_context, workspace={"scope_hash": SCOPE_HASH})
    request = replace(request, run_context=context, run_spec=replace(request.run_spec, context=context), capability_snapshot={"tools": ["tool-a", "tool-b"]})
    first = (await _collect(driver.start(request)))[0]
    assert first.calls[0].tool_name == "tool-a"

    second = (await _collect(driver.signal(ToolOutcomesSignal("run-react", first.command_id, (_outcome(first.calls[0]),)))))[0]

    assert second.calls[0].tool_name == "tool-b"
    record = await store.load_continuation("run-react")
    persisted = ReactCommandBoundary.from_record(record)
    assert persisted.pending_calls[0].tool_name == "tool-b"
    assert persisted.iteration == 4
    assert persisted.run_context == request.run_context
    assert persisted.run_spec == request.run_spec


def test_external_feedback_restores_gate_evidence_context_and_skill_once():
    from agent.context_manager import ContextManager
    from agent.harness_feedback import prepare_external_tool_feedback
    from agent.termination import GateConfig, TerminationGate
    from deskpet.agent.evidence_gate import EvidenceGate

    read = prepared_call(
        tool_name="read_file", model_args={"path": "README.md"},
        call_id="feedback-read", effect_id="effect-feedback-read",
        capability_hash=CAPABILITY_HASH, scope_hash=SCOPE_HASH,
    )
    skill = prepared_call(
        tool_name="skill_invoke", model_args={"skill_name": "review"},
        call_id="feedback-skill", effect_id="effect-feedback-skill",
        capability_hash=CAPABILITY_HASH, scope_hash=SCOPE_HASH,
    )
    boundary = ReactCommandBoundary(
        "run-react", "session-react", "feedback-command", "execute_tools",
        ({"role": "assistant", "tool_calls": []},), 0, None, None,
        (read, skill), (_context(read), _context(skill)),
        (NormalizedToolOutcome.success({"text": "evidence"}),
         NormalizedToolOutcome.success({"loaded": "review"})),
        {}, 3, {}, outcome_statuses=(OutcomeStatus.SUCCEEDED,) * 2,
    )
    loop = SimpleNamespace(
        _gate=TerminationGate(GateConfig(max_turns=20)),
        _ctx=ContextManager(), _evidence_gate=EvidenceGate(),
    )
    messages = boundary.canonical_messages + (
        {"role": "tool", "name": "read_file", "content": "read-result"},
        {"role": "tool", "name": "skill_invoke", "content": "skill-result"},
    )

    state, prepared_messages = prepare_external_tool_feedback(
        loop, boundary, {}, messages
    )
    state, _ = prepare_external_tool_feedback(loop, boundary, state, prepared_messages)

    feedback = state["agent_loop_feedback"]
    assert feedback["iteration"] == 3
    assert feedback["tools_used_count"] == 2
    assert feedback["evidence_gathered"] is True
    assert feedback["skills_used_order"] == ["review"]
    assert loop._gate.state.tools_used == 2
    assert loop._gate.state.turns_used == 3


def test_external_feedback_keeps_permanent_tool_failure_replannable():
    from agent.context_manager import ContextManager
    from agent.harness_feedback import prepare_external_tool_feedback
    from agent.termination import GateConfig, TerminationGate

    call = prepared_call(
        tool_name="write_file", model_args={"path": "missing/file"},
        call_id="feedback-failed", effect_id="effect-feedback-failed",
        capability_hash=CAPABILITY_HASH, scope_hash=SCOPE_HASH,
    )
    boundary = ReactCommandBoundary(
        "run-react", "session-react", "failed-command", "execute_tools",
        (), 0, None, None, (call,), (_context(call),),
        (NormalizedToolOutcome.failure(
            "invalid_argument", "missing required parameter: content"
        ),), {}, 1, {}, outcome_statuses=(OutcomeStatus.FAILED,),
    )
    loop = SimpleNamespace(
        _gate=TerminationGate(GateConfig(max_turns=20)), _ctx=ContextManager(),
        _evidence_gate=None,
    )

    state, _ = prepare_external_tool_feedback(
        loop, boundary, {}, ({"role": "tool", "name": "write_file", "content": "failed"},)
    )

    assert "agent_loop_terminal_error" not in state
    assert state["agent_loop_feedback"]["tool_history"][-1]["ok"] is False


def test_external_feedback_promotes_capture_bytes_to_visual_attachment():
    import base64
    import json

    from agent.context_manager import ContextManager
    from agent.harness_feedback import prepare_external_tool_feedback
    from agent.termination import GateConfig, TerminationGate
    from deskpet.agent.attachment_budget import collect_attachment_budget
    from deskpet.agent.tokens import count_messages_tokens

    call = prepared_call(
        tool_name="window_capture",
        model_args={"pid": 1, "creation_time": 2.0, "hwnd": 3},
        call_id="capture-call",
        effect_id="effect-capture",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )
    raw = b"\x89PNG\r\n\x1a\nvisual-proof"
    boundary = ReactCommandBoundary(
        "run-react",
        "session-react",
        "capture-command",
        "execute_tools",
        ({"role": "assistant", "tool_calls": []},),
        0,
        None,
        None,
        (call,),
        (_context(call),),
        (
            NormalizedToolOutcome.success(
                {
                    "ok": True,
                    "path": "captures/window.png",
                    "image_mime": "image/png",
                    "image_base64": base64.b64encode(raw).decode("ascii"),
                }
            ),
        ),
        {},
        1,
        {},
        outcome_statuses=(OutcomeStatus.SUCCEEDED,),
        provider_call_order=("capture-call",),
    )
    loop = SimpleNamespace(
        _gate=TerminationGate(GateConfig(max_turns=20)),
        _ctx=ContextManager(),
        _evidence_gate=None,
    )

    _, prepared = prepare_external_tool_feedback(
        loop, boundary, {}, ReActDriver._tool_messages(boundary)
    )

    tool_payload = json.loads(prepared[-2]["content"])
    value = tool_payload["outcome"]["value"]
    assert "image_base64" not in value
    assert value["media_attachment"]["attached"] is True
    assert value["media_attachment"]["bytes"] == len(raw)
    assert prepared[-1]["role"] == "user"
    assert prepared[-1]["content"][1]["type"] == "image_url"
    assert prepared[-1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    refs, attachment_tokens = collect_attachment_budget(prepared)
    assert len(refs) == 1
    assert attachment_tokens > 0
    assert count_messages_tokens(list(prepared)) < 1_000


@pytest.mark.asyncio
async def test_recovery_second_batch_is_fenced_by_current_lease(tmp_path):
    first_call = _call(0, durable=True)
    second_call = _call(1, durable=False)
    first_batch = ReactToolBatch("first-batch", (first_call,), (_context(first_call),))
    second_batch = ReactToolBatch("second-batch", (second_call,), (_context(second_call),))
    first, store, path = await _driver(tmp_path, ScriptedCollaborator([first_batch]))
    await _collect(first.start(_request()))
    await _record_effect(path, first_call, _outcome(first_call), "succeeded")
    old_lease = await _recovery_lease(store)

    class TakeoverCollaborator(ScriptedCollaborator):
        async def resume(self, boundary, response):
            await store.recovery_scope(old_lease, lease_seconds=None)
            self.current_lease = await store.recovery_scope("run-react", owner="takeover")
            yield second_batch

    takeover = TakeoverCollaborator()
    stale = _react_driver(takeover, store)
    with pytest.raises(StaleRecoveryLease):
        await _collect(stale.recover("run-react", old_lease))
    assert ReactCommandBoundary.from_record(await store.load_continuation("run-react")).command_id == "first-batch"

    current = _react_driver(ScriptedCollaborator(resumes=[[second_batch]]), store)
    candidates = await _collect(current.recover("run-react", takeover.current_lease))
    assert candidates[0].command_id == "second-batch"
    assert ReactCommandBoundary.from_record(await store.load_continuation("run-react")).command_id == "second-batch"


@pytest.mark.asyncio
async def test_repair_refresh_automatically_reprepares_original_call(tmp_path):
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.activate_runtime()
    registry = ToolRegistry()
    registry.register(
        "photo_rename",
        "rename a photo",
        {
            "name": "photo_rename",
            "description": "rename a photo",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        lambda args, task_id: {"ok": True, "args": args, "task_id": task_id},
    )
    collaborator = ScriptedCollaborator()
    driver = ReActDriver(collaborator, store, registry)
    _bind_live(driver)
    repair_call = _call(0, durable=True)
    repair_context = _context(repair_call)
    original_args = {"path": "F:/photos/a.jpg"}
    failed_effect_id = "e" * 64
    boundary = ReactCommandBoundary(
        run_id="run-react",
        session_id="session-react",
        command_id="repair-command",
        command_kind="control_delegate",
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": [{"id": repair_call.stable_call_id}],
            },
        ),
        session_projection_cursor=0,
        prepared_context_ref="prepared-context-v1",
        tool_set_snapshot_ref="tool-set-v2",
        pending_calls=(repair_call,),
        tool_contexts=(repair_context,),
        outcomes=(NormalizedToolOutcome.success({"repaired": True}),),
        outcome_statuses=(OutcomeStatus.SUCCEEDED,),
        provider_state={},
        iteration=3,
        completion_state={
            "model_backfilled": False,
            "pending_capability_retry": {
                "failure_receipt_ref": "f" * 64,
                "tool_name": "photo_rename",
                "canonical_args": original_args,
                "args_hash": fingerprint_json(original_args),
                "retry_of_effect_id": failed_effect_id,
                "failed_tool_spec_fingerprint": "a" * 64,
            },
        },
        capability_snapshot={"tools": ["photo_rename"]},
        run_context=_request().run_context,
        run_spec=_request().run_spec,
    )
    active = driver._live.get("run-react")
    assert active is not None
    active.driver_state = boundary

    candidates = await _collect(driver._resume_completed(boundary))

    assert len(candidates) == 1
    execute = candidates[0]
    assert execute.kind == "execute_tools"
    assert len(execute.calls) == 1
    retried = execute.calls[0]
    assert retried.tool_name == "photo_rename"
    assert dict(retried.final_params) == original_args
    assert retried.retry_of_effect_id == failed_effect_id
    assert retried.tool_spec_fingerprint != "a" * 64
    assert execute.contexts[0].effect_id != failed_effect_id
    assert collaborator.resume_inputs == []
    persisted = ReactCommandBoundary.from_record(
        await store.load_continuation("run-react")
    )
    assert "pending_capability_retry" not in persisted.completion_state
    assert (
        persisted.completion_state["capability_retry_inflight"][
            "retry_of_effect_id"
        ]
        == failed_effect_id
    )
    assert persisted.canonical_messages[-2]["role"] == "tool"
    assert persisted.canonical_messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_refresh_failure_returns_trusted_failure_to_same_model(tmp_path):
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.activate_runtime()
    capability_store = CapabilityStore(store)
    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("replanned")]])

    class FailingRefreshService:
        def __init__(self, backing_store):
            self.store = backing_store

        async def refresh(self, *args, **kwargs):
            del args, kwargs
            raise CapabilityRefreshError("refresh_fixture_failed", "boom")

    driver = ReActDriver(
        collaborator,
        store,
        PolicyRegistry(),
        capability_refresh_service=FailingRefreshService(capability_store),
        capability_refresh_staging=CapabilityRefreshStagingService(
            capability_store
        ),
    )
    _bind_live(driver)
    call = _call(0, durable=True)
    context = _context(call)
    request = _request()
    batch = ReactToolBatch(
        command_id="mutation-command",
        calls=(call,),
        contexts=(context,),
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": [{"id": call.stable_call_id}],
            },
        ),
    )
    boundary = driver._boundary_for_batch(request, batch)
    stamp = CatalogStamp(
        catalog_generation=0,
        registry_revision=0,
        binding_generation=0,
        skill_revision=0,
        mcp_revision=0,
    )
    receipt = CapabilityOperationReceipt(
        operation_id="refresh-failure-operation",
        root_run_id="run-react",
        parent_command_id=boundary.command_id,
        parent_effect_id=context.effect_id,
        action="repair",
        refresh_nonce="refresh-failure-nonce",
        old_stamp=stamp,
        published_binding_generation=1,
        affected_capability_ids=("photo_renamer",),
        affected_version_refs=("photo_renamer@2.0.0:" + "a" * 64,),
        affected_tool_spec_fingerprints=("b" * 64,),
        manifest_hashes=("a" * 64,),
    )
    await capability_store.create_operation(
        operation_id=receipt.operation_id,
        idempotency_key="refresh-failure-idempotency",
        kind="repair",
        request={"fixture": True},
        root_run_id="run-react",
        pack_id="photo_renamer",
        requested_scope="run",
        requested_scope_key="run-react",
    )
    for phase in CAPABILITY_OPERATION_PHASES:
        await capability_store.commit_phase(
            receipt.operation_id,
            phase,
            evidence={"fixture": True, "phase": phase},
        )
    await capability_store.put_operation_receipt(receipt)
    intent = CapabilityRefreshIntent(
        intent_id="refresh-failure-intent",
        root_run_id="run-react",
        run_id="run-react",
        operation_id=receipt.operation_id,
        source_kind="tool_effect",
        source_command_id=boundary.command_id,
        source_effect_id=context.effect_id,
        refresh_nonce=receipt.refresh_nonce,
        expected_continuation_version=1,
        old_stamp=stamp,
        old_catalog_snapshot_ref="c" * 64,
        old_tool_set_snapshot_ref="d" * 64,
        exposure_intent_ref="e" * 64,
    )
    async with capability_store.write_transaction() as db:
        await capability_store.bind(db).stage_refresh_intent(intent)
    boundary = replace(
        boundary,
        outcomes=(NormalizedToolOutcome.success({"published": True}),),
        outcome_statuses=(OutcomeStatus.SUCCEEDED,),
        request_payload={
            "task_scope_id": "task-react",
            "capability_refresh": {
                "catalog_stamp": stamp.to_dict(),
                "catalog_snapshot_ref": "c" * 64,
                "tool_set_snapshot_ref": "d" * 64,
                "exposure_intent_ref": "e" * 64,
                "refresh_pending": intent.intent_id,
                "provider_backfill_blocked": True,
                "scope": {
                    "run_key": "run-react",
                    "project_key": "F:/workspace",
                    "user_key": "default",
                    "builtin_key": "builtin",
                },
            },
        },
    )
    persisted = await driver._persist_boundary(request, boundary)
    await driver._admit_provider_boundary(persisted)

    candidates = await _collect(driver._resume_completed(persisted))

    assert candidates == [
        DriverTerminalCandidate("run-react", "completed", "replanned")
    ]
    failed_intent = await capability_store.get_refresh_intent(intent.intent_id)
    assert failed_intent is not None
    assert failed_intent.status == "failed"
    assert len(collaborator.resume_inputs) == 1
    resumed_boundary = collaborator.resume_inputs[0][0]
    assert resumed_boundary.run_id == "run-react"
    tool_payload = json.loads(resumed_boundary.canonical_messages[-1]["content"])
    assert tool_payload["outcome"]["error"]["code"] == "refresh_fixture_failed"
    assert tool_payload["failure_report"]["error_code"] == "refresh_fixture_failed"
    assert (
        resumed_boundary.request_payload["capability_refresh"][
            "refresh_pending"
        ]
        is None
    )


def test_react_driver_module_has_no_product_tool_or_workflow_dependencies():
    from pathlib import Path

    source = Path("backend/deskpet/harness/drivers/react.py").read_text(encoding="utf-8")
    forbidden = (
        "ppt_tools",
        "research_tools",
        "deep_research",
        "deskpet.workflows.engine",
        "deskpet.workflows.definitions",
        "SessionDB",
        "websocket",
    )
    assert not [name for name in forbidden if name in source]
