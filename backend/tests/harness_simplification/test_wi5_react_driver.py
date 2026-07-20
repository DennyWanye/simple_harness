from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import aiosqlite
import pytest

from deskpet.execution import (
    PersistenceLevel,
    RunContext,
    RunCreate,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.harness.continuations import SqliteReactCommandBoundaryStore
from deskpet.harness.drivers.react import (
    LegacyAgentLoopCollaborator,
    LegacyAgentLoopToolInterceptionError,
    ReActDriver,
    ReactDecisionRequest,
    ReactDelegateRequest,
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
    ToolOutcomesSignal,
)
from deskpet.harness.tool_executor import (
    PreparedExecutionCall,
    ToolOutcome,
    ToolOutcomeStatus,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.store import SqliteExecutionUnitOfWork


CAPABILITY_HASH = fingerprint_json({"tools": ["read", "write"]})
SCOPE_HASH = fingerprint_json({"workspace": "F:/workspace"})


async def _collect(iterator: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in iterator]


def _request(run_id: str = "run-react") -> DriverStart:
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


class DurablePromoter:
    def __init__(self, path) -> None:
        self.uow = SqliteExecutionUnitOfWork(path)
        self.calls: list[str] = []

    async def promote_for_boundary(self, run_id: str) -> None:
        self.calls.append(run_id)
        await self.uow.create(_run_spec(run_id))


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


class EffectReader:
    def __init__(self, outcomes: Mapping[str, ToolOutcome] | None = None) -> None:
        self.outcomes = dict(outcomes or {})
        self.reads: list[str] = []

    async def get_outcome(self, effect_id: str) -> ToolOutcome | None:
        self.reads.append(effect_id)
        return self.outcomes.get(effect_id)


class Reconciler:
    def __init__(self, outcomes: Mapping[str, ToolOutcome]) -> None:
        self.outcomes = dict(outcomes)
        self.calls: list[str] = []

    async def reconcile(self, call, context, outcome) -> ToolOutcome:
        self.calls.append(call.effect_id)
        return self.outcomes[call.effect_id]


class RecordingOutcomeCommitter:
    def __init__(self, store) -> None:
        self.store = store
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    async def commit_outcomes(self, boundary, updates):
        self.calls.append((boundary.command_id, tuple(updates)))
        updated = boundary.with_outcomes(updates)
        await self.store.put(updated)
        return updated


def _call(index: int, *, durable: bool) -> PreparedExecutionCall:
    return PreparedExecutionCall(
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


def _context(call: PreparedExecutionCall) -> ToolExecutionContext:
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
        call_id=call.call_id,
        effect_id=call.effect_id,
        trace_id="trace-run-react",
    )


def _outcome(
    call: PreparedExecutionCall,
    *,
    status: ToolOutcomeStatus = ToolOutcomeStatus.SUCCEEDED,
) -> ToolOutcome:
    return ToolOutcome(
        call_id=call.call_id,
        effect_id=call.effect_id,
        status=status,
        value={"completed": call.call_id},
        receipt_ref=f"receipt:{call.effect_id}",
    )


def _driver(tmp_path, collaborator, *, reader=None, reconciler=None):
    path = tmp_path / "workflow.db"
    promoter = DurablePromoter(path)
    store = SqliteReactCommandBoundaryStore(path)
    return (
        ReActDriver(
            collaborator,
            store,
            promoter,
            reader or EffectReader(),
            reconciler=reconciler,
        ),
        store,
        promoter,
        path,
    )


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
    driver, _, promoter, _ = _driver(tmp_path, collaborator)

    candidates = await _collect(driver.start(_request()))

    assert candidates == [
        TokenCandidate("run-react", "first"),
        ProviderFallbackCandidate("run-react", "primary", "backup", "timeout"),
        TokenCandidate("run-react", "second"),
        DriverTerminalCandidate("run-react", "completed", "done"),
    ]
    assert promoter.calls == []


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
    first, store, promoter, path = _driver(
        tmp_path, ScriptedCollaborator([ReactDecisionRequest(decision)])
    )
    assert await _collect(first.start(_request())) == [decision]
    persisted = await store.load("run-react")
    assert persisted is not None
    assert persisted.session_id == "session-react"
    assert persisted.pending_decision == decision
    assert promoter.calls == ["run-react"]

    resumed = ScriptedCollaborator(resumes=[[ReactFinal("answered")]])
    second = ReActDriver(
        resumed,
        store,
        DurablePromoter(path),
        EffectReader(),
    )
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
async def test_recovery_backfills_successful_effect_without_regenerating_or_reexecuting(tmp_path):
    call = _call(0, durable=True)
    batch = ReactToolBatch("batch-1", (call,), (_context(call),))
    first, store, _, path = _driver(tmp_path, ScriptedCollaborator([batch]))
    assert (await _collect(first.start(_request())))[0].calls == (call,)

    # The external executor committed the write, then the process crashed
    # before ToolOutcomesSignal could backfill the model transcript.
    committed = _outcome(call)
    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("recovered")]])
    outcome_committer = RecordingOutcomeCommitter(store)
    second = ReActDriver(
        collaborator,
        store,
        DurablePromoter(path),
        EffectReader({call.effect_id: committed}),
        outcome_committer=outcome_committer,
    )
    candidates = await _collect(second.recover("run-react"))

    assert candidates == [DriverTerminalCandidate("run-react", "completed", "recovered")]
    boundary, response = collaborator.resume_inputs[0]
    assert boundary.outcomes == (committed,)
    assert response == {"type": "tool_outcomes", "command_id": "batch-1"}
    tool_message = boundary.canonical_messages[-1]
    assert tool_message["tool_call_id"] == call.call_id
    assert json.loads(tool_message["content"])["effect_id"] == call.effect_id
    persisted = await store.load("run-react")
    assert persisted is not None
    assert persisted.completion_state["model_backfilled"] is True
    assert outcome_committer.calls == [("batch-1", (0,))]


@pytest.mark.asyncio
async def test_mixed_batch_freezes_full_order_and_recovers_only_missing_calls(tmp_path):
    calls = (_call(0, durable=False), _call(1, durable=True), _call(2, durable=False))
    batch = ReactToolBatch("mixed", calls, tuple(_context(call) for call in calls))
    first, store, promoter, path = _driver(tmp_path, ScriptedCollaborator([batch]))
    initial = await _collect(first.start(_request()))
    assert initial == [ExecuteTools("run-react", "mixed", calls, batch.contexts, (0, 1, 2))]
    assert promoter.calls == ["run-react"]
    persisted = await store.load("run-react")
    assert persisted is not None
    assert persisted.pending_calls == calls

    after_first = await _collect(
        first.signal(ToolOutcomesSignal("run-react", "mixed", (_outcome(calls[0]),)))
    )
    assert after_first[0].calls == calls[1:]
    assert after_first[0].original_indexes == (1, 2)

    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("all done")]])
    second = ReActDriver(
        collaborator,
        store,
        DurablePromoter(path),
        EffectReader({calls[1].effect_id: _outcome(calls[1])}),
    )
    recovered = await _collect(second.recover("run-react"))
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
async def test_unknown_effect_is_reconciled_before_driver_resumes(tmp_path):
    call = _call(0, durable=True)
    batch = ReactToolBatch("batch-unknown", (call,), (_context(call),))
    first, store, _, path = _driver(tmp_path, ScriptedCollaborator([batch]))
    await _collect(first.start(_request()))
    unknown = ToolOutcome.unknown(call, "process_exit")
    reconciled = _outcome(call)
    reconciler = Reconciler({call.effect_id: reconciled})
    collaborator = ScriptedCollaborator(resumes=[[ReactFinal("reconciled")]])
    second = ReActDriver(
        collaborator,
        store,
        DurablePromoter(path),
        EffectReader({call.effect_id: unknown}),
        reconciler=reconciler,
    )

    candidates = await _collect(second.recover("run-react"))

    assert candidates == [DriverTerminalCandidate("run-react", "completed", "reconciled")]
    assert reconciler.calls == [call.effect_id]
    assert collaborator.resume_inputs[0][0].outcomes == (reconciled,)


@pytest.mark.asyncio
async def test_delegate_boundary_survives_restart_and_accept_signal(tmp_path):
    command = _delegate(JoinPolicy.DETACHED)
    first, store, promoter, path = _driver(
        tmp_path,
        ScriptedCollaborator([ReactDelegateRequest(command)]),
    )
    assert await _collect(first.start(_request())) == [command]
    assert promoter.calls == ["run-react"]
    persisted = await store.load("run-react")
    assert persisted is not None and persisted.pending_delegate == command

    resumed = ScriptedCollaborator(resumes=[[ReactFinal("detached accepted")]])
    second = ReActDriver(
        resumed,
        store,
        DurablePromoter(path),
        EffectReader(),
    )
    assert await _collect(second.recover("run-react")) == [command]
    candidates = await _collect(
        second.signal(ChildAcceptedSignal("run-react", command.command_id, "child-1"))
    )
    assert candidates == [
        ChildAcceptedCandidate(
            "run-react", command.command_id, "child-1", JoinPolicy.DETACHED
        ),
        DriverTerminalCandidate("run-react", "completed", "detached accepted"),
    ]


def _delegate(join_policy: JoinPolicy) -> DelegateRun:
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
        [ReactDelegateRequest(command)],
        resumes=[[ReactFinal(f"resumed-{join_policy.value}")]],
    )
    driver, _, _, _ = _driver(tmp_path, collaborator)
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
async def test_cancel_waits_for_collaborator_acknowledgement(tmp_path):
    collaborator = ScriptedCollaborator()
    driver, _, _, _ = _driver(tmp_path, collaborator)

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
    }


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
            return PreparedExecutionCall(
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
