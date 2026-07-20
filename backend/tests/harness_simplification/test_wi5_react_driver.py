from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from deskpet.execution import (
    ActorContext,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.execution.evidence import EvidenceContext, UNKNOWN_EVIDENCE
from deskpet.harness.child_runs import (
    ChildRunCoordinator,
    ChildRunScheduler as _ChildRunScheduler,
)
from deskpet.harness.drivers.react import (
    LegacyAgentLoopCollaborator,
    LegacyAgentLoopToolInterceptionError,
    ReActDriver,
    ReactCommandBoundary,
    ReactEmission,
    ReactFallback,
    ReactFinal,
    ReactToken,
    ReactToolBatch,
)
from deskpet.harness.ports import (
    AttachmentPolicy,
    CancelAcknowledgedCandidate,
    ChildAcceptedCandidate,
    ChildAcceptedSignal,
    ChildTerminalSignal,
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
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall
from deskpet.workflows.store import SqliteExecutionUnitOfWork, StaleRecoveryLease


CAPABILITY_HASH = fingerprint_json({"tools": ["read", "write"]})
SCOPE_HASH = fingerprint_json({"workspace": "F:/workspace"})


class ChildRunScheduler(_ChildRunScheduler):
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
    return tuple(ChildRunScheduler._signal(record) for record in records)


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


async def _driver(tmp_path, collaborator, *, reader=None, reconciler=None):
    path = tmp_path / "workflow.db"
    store = SqliteExecutionUnitOfWork(path)
    await store.activate_empty_runtime()
    return (
        ReActDriver(
            collaborator,
            store,
            PolicyRegistry(),
        ),
        store,
        path,
    )


async def _recovery_lease(store):
    return await store.claim_recovery("run-react", owner="react-test-recovery")


async def _record_effect(path, call: PreparedToolCall, outcome: NormalizedToolOutcome, status: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("""INSERT INTO execution_effects(
            effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
            args_hash,capability_hash,scope_hash,effect_type,status,policy_json,
            prepared_json,outcome_json,artifact_refs_json,effect_version,created_at,updated_at,ended_at)
            VALUES(?,1,?,?,?,?,?,?,?,?,?,'{}',?,?,'[]',1,1,1,1)""",
            (_EFFECTS[call.stable_call_id], "run-react", "fp", call.stable_call_id,
             call.tool_name, call.args_hash, CAPABILITY_HASH, SCOPE_HASH, call.effect_type,
             status, json.dumps(call.to_dict()), json.dumps(outcome.to_dict())))
        await db.commit()


class _ChildLauncher:
    async def accept(self, command) -> None:
        return None

    async def deliver(self, parent, delivery, recovery_lease) -> None:
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
    await ChildRunScheduler(
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
    second = ReActDriver(
        resumed,
        store,
        PolicyRegistry(),
    )
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE execution_decisions
            SET status='allowed',response_schema_version=1,response_json=?,
                decision_version=decision_version+1,resolved_at=1.0
            WHERE decision_id='decision-1'""",
            (json.dumps({"answer": "F:/workspace"}),),
        )
        await db.commit()
    candidates = await _collect(
        second.signal(
            DecisionSignal("run-react", "decision-1", {"answer": "F:/workspace"})
        )
    )

    assert candidates == [DriverTerminalCandidate("run-react", "completed", "answered")]
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
        model_args=dict(call.final_params),
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
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE execution_decisions
            SET status='allowed',response_schema_version=1,response_json='{"allow":true}',
                decision_version=decision_version+1,resolved_at=1.0
            WHERE decision_id=?""",
            (decision.decision_id,),
        )
        await db.commit()

    resumed = await _collect(
        driver.signal(
            DecisionSignal(
                "run-react",
                decision.decision_id,
                {"allow": True, "grant_id": "grant-1", "grant_version": 0},
                nonce=decision.nonce,
                version=0,
            )
        )
    )

    assert len(resumed) == 1
    command = resumed[0]
    assert command.kind == "execute_tools"
    assert command.grant_refs[0].grant_id == "grant-1"
    assert command.grant_refs[0].decision_nonce == decision.nonce


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
    second = ReActDriver(
        collaborator,
        store,
        PolicyRegistry(),
    )
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
            args_hash,capability_hash,scope_hash,effect_type,status,policy_json,
            prepared_json,outcome_json,receipt_ref,artifact_refs_json,
            effect_version,created_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_EFFECTS[call.stable_call_id], 1, "run-react", "effect-fingerprint", call.stable_call_id,
             call.tool_name, call.args_hash, CAPABILITY_HASH, SCOPE_HASH, "write",
             "succeeded", "{}", "{}", json.dumps(outcome.to_dict()), _OUTCOME_METADATA[id(outcome)]["receipt_ref"],
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

    after_first = await _collect(
        first.signal(ToolOutcomesSignal("run-react", "mixed", (_outcome(calls[0]),)))
    )
    assert after_first[0].calls == calls[1:]
    assert after_first[0].original_indexes == (1, 2)
    await _record_effect(path, calls[1], _outcome(calls[1]), "succeeded")

    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("all done")]])
    second = ReActDriver(
        collaborator,
        store,
        PolicyRegistry(),
    )
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
    second = ReActDriver(collaborator, store, PolicyRegistry())

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
    second = ReActDriver(
        resumed,
        store,
        PolicyRegistry(),
    )
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

    await _collect(ReActDriver(LegacyAgentLoopCollaborator(loop), store, PolicyRegistry()).signal(accepted))

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

    await _collect(ReActDriver(LegacyAgentLoopCollaborator(loop), store, PolicyRegistry()).signal(terminal))

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
        await _collect(ReActDriver(CrashOnResume(), store, PolicyRegistry()).signal(terminal))
    loop = _CaptureLoop()
    restarted = ReActDriver(LegacyAgentLoopCollaborator(loop), store, PolicyRegistry())
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
    restarted = ReActDriver(ScriptedCollaborator(), store, PolicyRegistry())

    assert await _collect(restarted.recover("run-react", await _recovery_lease(store))) == [
        DriverTerminalCandidate(
            "run-react", "completed", "root child result",
            correlation={"child_run_id": terminal.child_run_id},
        )
    ]


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
            yield FinalEvent(content="done")

        def run(self, messages, **kwargs):
            self.seen = (messages, kwargs)
            return self._events()

    loop = FakeLoop()
    collaborator = LegacyAgentLoopCollaborator(loop)
    emissions = await _collect(collaborator.start(_request()))

    assert emissions == [
        ReactToken("first"),
        ReactFallback("primary", "backup", "timeout"),
        ReactFinal("done"),
    ]
    assert loop.seen is not None
    assert loop.seen[1] == {
        "task_id": "run-react",
        "session_id": "session-react",
        "stream": True,
        "_scoped_evidence": UNKNOWN_EVIDENCE,
    }


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

        def prepare_execution_call(self, *args, **kwargs):
            self.prepared += 1
            raise AssertionError("denied tool must not reach prepare")

    llm = LLM()
    tools = Tools()
    collaborator = LegacyAgentLoopCollaborator(
        AgentLoop(llm, tools, external_tool_dispatch=True), call_factory=tools
    )
    request = replace(
        _request(), capability_snapshot={"tools": ["allowed"]}
    )

    with pytest.raises(
        LegacyAgentLoopToolInterceptionError,
        match="outside capability snapshot",
    ):
        await _collect(collaborator.start(request))

    assert [item["function"]["name"] for item in llm.seen_tools] == ["allowed"]
    assert tools.prepared == 0


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
    await _collect(LegacyAgentLoopCollaborator(loop).resume(restored, {}))
    assert loop.kwargs["tool_names_filter"] == ["read_file"]


@pytest.mark.asyncio
async def test_legacy_collaborator_fails_closed_before_legacy_tool_dispatch_can_leak():
    from agent.agent_loop import ToolCallEvent

    class FakeLoop:
        async def _events(self):
            yield ToolCallEvent()

        def run(self, messages, **kwargs):
            return self._events()

    collaborator = LegacyAgentLoopCollaborator(FakeLoop())
    with pytest.raises(LegacyAgentLoopToolInterceptionError):
        await _collect(collaborator.start(_request()))


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

        def prepare_execution_call(
            self, tool_name, raw_args, *, call_id, effect_id, context
        ):
            return prepared_call(
                tool_name=tool_name,
                model_args=raw_args,
                call_id=call_id,
                effect_id=effect_id,
                capability_hash=context.capability_hash,
                scope_hash=context.scope_hash,
            )

    tools = Tools()
    loop = AgentLoop(LLM(), tools, external_tool_dispatch=True)
    collaborator = LegacyAgentLoopCollaborator(loop, call_factory=tools)
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

    emissions = await _collect(collaborator.start(request))

    assert tools.executed == 0
    assert len(emissions) == 1
    batch = emissions[0]
    assert isinstance(batch, ReactToolBatch)
    assert [call.tool_name for call in batch.calls] == ["probe"]
    assert batch.canonical_messages[-1]["role"] == "assistant"


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

        def prepare_execution_call(self, tool_name, raw_args, *, call_id, effect_id, context):
            return prepared_call(tool_name=tool_name, model_args=raw_args, call_id=call_id, effect_id=effect_id, capability_hash=context.capability_hash, scope_hash=context.scope_hash, recoverable_effect=True)

    tools = Tools()
    collaborator = LegacyAgentLoopCollaborator(AgentLoop(LLM(), tools, external_tool_dispatch=True), call_factory=tools)
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
    assert persisted.run_context == request.run_context
    assert persisted.run_spec == request.run_spec


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
            await store.release_recovery(old_lease)
            self.current_lease = await store.claim_recovery("run-react", owner="takeover")
            yield second_batch

    takeover = TakeoverCollaborator()
    stale = ReActDriver(takeover, store, PolicyRegistry())
    with pytest.raises(StaleRecoveryLease):
        await _collect(stale.recover("run-react", old_lease))
    assert ReactCommandBoundary.from_record(await store.load_continuation("run-react")).command_id == "first-batch"

    current = ReActDriver(ScriptedCollaborator(resumes=[[second_batch]]), store, PolicyRegistry())
    candidates = await _collect(current.recover("run-react", takeover.current_lease))
    assert candidates[0].command_id == "second-batch"
    assert ReactCommandBoundary.from_record(await store.load_continuation("run-react")).command_id == "second-batch"


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
