from __future__ import annotations

import json
from collections import Counter

import pytest

from deskpet.workflows import WorkflowContext, WorkflowRunStatus, compile_workflow
from deskpet.workflows.definitions.code_nodes import (
    CapabilitySnapshotV1,
    WorkflowSessionRefV1,
)
from deskpet.workflows.definitions.v1 import (
    CODE_COMPLEX_V1,
    CODE_COMPLEX_V1_DEFINITION,
    code_complex_initial_state,
    register_v1_workflows,
)
from deskpet.workflows.effects import PreparedToolCall
from deskpet.workflows.errors import WorkflowErrorCode, WorkflowNodeError
from deskpet.workflows.outbox import WorkflowOutbox
from deskpet.workflows.progress import WorkflowProgressReporter
from deskpet.workflows.proposal_state import (
    ConvergenceStateV1,
    GateConfigV1,
    GateStateV1,
    ProposalOutcomeV1,
    ProposalStateV1,
)
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore
from tests.test_workflow_native_engine import MemoryNativeStore
from deskpet.memory.session_db import SessionDB


def _session_ref() -> WorkflowSessionRefV1:
    return WorkflowSessionRefV1(
        base_session_id="base-session",
        code_session_id="code-session",
        delivery_session_id="delivery-session",
        project_root_hash="project-root-hash",
        base_epoch=2,
        code_epoch=4,
    )


def _capability(tool_name: str = "write_file") -> CapabilitySnapshotV1:
    return CapabilitySnapshotV1(
        tool_name=tool_name,
        source="builtin",
        schema_hash=f"schema-{tool_name}",
        spec_version="1",
        effect_policy={"kind": "staged_file", "version": "1"},
        lifecycle_hash="lifecycle-v1",
        outcome_parser_hash="outcome-v1",
    )


def _prepared(call_id: str, *, tool_name: str = "write_file", path: str = "file.txt") -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name=tool_name,
        stable_call_id=call_id,
        final_params={"path": path, "content": call_id},
        tool_spec_version="1",
        schema_hash=f"schema-{tool_name}",
        permission_policy_version="permission-v1",
        effect_type="staged_file",
    )


def _outcome(
    *calls: PreparedToolCall,
    content: str = "",
    source: str = "builtin",
    access: str = "write",
    stop_reason: str | None = None,
) -> ProposalOutcomeV1:
    return ProposalOutcomeV1(
        assistant_content=content,
        reasoning_summary_ref="reasoning:fixture",
        raw_tool_proposals=[
            {
                "stable_call_id": call.stable_call_id,
                "tool_name": call.tool_name,
                "raw_params": dict(call.final_params),
                "source": source,
                "access": access,
            }
            for call in calls
        ],
        prepared_calls=list(calls),
        stop_reason=stop_reason or ("tool_calls" if calls else "stop"),
        usage={"input_tokens": 20, "output_tokens": 10},
        provider="fake-provider",
        model="fake-model",
    )


class FakeProposer:
    def __init__(self, outcomes: list[ProposalOutcomeV1]) -> None:
        self.outcomes = list(outcomes)
        self.states: list[ProposalStateV1] = []

    async def propose(self, state: ProposalStateV1) -> ProposalOutcomeV1:
        self.states.append(state)
        if not self.outcomes:
            raise AssertionError("unexpected proposal call")
        return self.outcomes.pop(0)


class JournaledDispatch:
    def __init__(self, *, crash_after_first_new_effect: bool = False) -> None:
        self.journal: dict[str, dict[str, object]] = {}
        self.attempts: Counter[str] = Counter()
        self.effect_commits: Counter[str] = Counter()
        self.crash_after_first_new_effect = crash_after_first_new_effect
        self.crashed = False
        self.authorizations: list[dict[str, object]] = []

    async def dispatch(
        self,
        prepared_calls,
        *,
        workflow_step_id,
        prior_results,
        authorizations,
    ):
        del prior_results
        self.authorizations.append(dict(authorizations))
        results: dict[str, object] = {}
        for call in prepared_calls:
            self.attempts[call.stable_call_id] += 1
            if call.stable_call_id not in self.journal:
                self.journal[call.stable_call_id] = {
                    "status": "success",
                    "effect_commit": call.stable_call_id,
                    "step_seen": workflow_step_id,
                }
                self.effect_commits[call.stable_call_id] += 1
                if self.crash_after_first_new_effect and not self.crashed:
                    self.crashed = True
                    raise WorkflowNodeError(
                        code=WorkflowErrorCode.RETRYABLE_PROVIDER,
                        message_ref="test:dispatch_crash",
                        node_id="tool_execution",
                    )
            results[call.stable_call_id] = self.journal[call.stable_call_id]
        return results


class FakeEvaluator:
    def __init__(self, test_results: list[dict[str, object]] | None = None) -> None:
        self.test_results = list(test_results or [{"passed": True, "evidence_refs": ["evidence:test"]}])
        self.audit_calls = 0

    async def run_tests(self, state: ProposalStateV1):
        del state
        return self.test_results.pop(0)

    async def audit(self, audit, state: ProposalStateV1):
        del state
        self.audit_calls += 1
        return audit


class _ProgressRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def report(self, identity, transition):
        self.events.append((identity.node_id, transition))


def _context(proposer, dispatch, evaluator=None, progress=None) -> WorkflowContext:
    ports = {"llm": proposer, "tool": dispatch}
    if evaluator is not None:
        ports["evaluator"] = evaluator
    if progress is not None:
        ports["progress"] = progress
    return WorkflowContext(ports=ports, request_id="request", turn_id="turn")


async def _runner(tmp_path, *, owner: str):
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database)
    saver = FencedAsyncSqliteSaver(database)
    registry = WorkflowRegistry()
    register_v1_workflows(registry)
    runner = WorkflowRunner(store, saver, registry, owner=owner)
    run_id = await runner.start(
        session_id="base-session",
        request_id="request",
        turn_id="turn",
        workflow_name="code_complex",
        workflow_version="v1",
        capability_snapshot={"tools": [_capability().to_dict()]},
    )
    return runner, store, run_id


def _state(run_id: str, **overrides):
    values = {
        "request": "Implement the durable code change",
        "run_id": run_id,
        "session_ref": _session_ref(),
        "capability_snapshot": [_capability()],
        "approval_required": False,
        "plan_steps": ["Implement and verify the change"],
    }
    values.update(overrides)
    return code_complex_initial_state(**values)


def _interrupt_id(output: object) -> str:
    assert isinstance(output, dict)
    return str(output["interrupt"]["interrupt_id"])


@pytest.mark.asyncio
async def test_compiled_code_progress_persists_to_original_session(tmp_path) -> None:
    session_id = "base-session"
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    await session_db.ensure_session(session_id)
    runner, store, run_id = await _runner(tmp_path, owner="progress-code")
    await store.bind_session_refs(run_id, (("delivery", session_id, 0),))

    async def persist(event, delivery) -> None:
        await session_db.append_message_if_epoch(
            delivery["target_id"],
            "assistant",
            str(event["payload"]["text"]),
            expected_epoch=0,
            workflow_event_id=str(event["event_id"]),
        )

    outbox = WorkflowOutbox(store)
    service = WorkflowService(
        store,
        runner=runner,
        outbox=outbox,
        delivery_handlers={"session_message": persist},
    )
    progress = WorkflowProgressReporter(
        service, (("session_message", session_id),)
    )
    proposer = FakeProposer(
        [_outcome(_prepared("progress-call")), _outcome(content="Done")]
    )
    result = await runner.run(
        run_id,
        _state(run_id),
        _context(proposer, JournaledDispatch(), FakeEvaluator(), progress),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    messages = await session_db.get_messages(session_id)
    event_ids = [message["workflow_event_id"] for message in messages]
    assert len(event_ids) == len(set(event_ids))
    events = await outbox.events_after(run_id, 0, limit=100)
    ordinals = {
        event["payload"]["ordinal"]
        for event in events["events"]
        if event["event_type"] == "workflow.progress"
    }
    assert ordinals == set(range(1, 10))


@pytest.mark.asyncio
async def test_proposal_checkpoint_restart_does_not_repeat_llm_and_reuses_partial_effects(
    tmp_path,
) -> None:
    call_a = _prepared("call-a", path="a.txt")
    call_b = _prepared("call-b", path="b.txt")
    proposer = FakeProposer([_outcome(call_a, call_b), _outcome(content="All work is complete.")])
    dispatch = JournaledDispatch(crash_after_first_new_effect=True)
    evaluator = FakeEvaluator()
    progress = _ProgressRecorder()
    context = _context(proposer, dispatch, evaluator, progress)
    runner, store, run_id = await _runner(tmp_path, owner="before-crash")

    failed = await runner.run(run_id, _state(run_id), context)
    history = await runner.get_state_history(run_id)

    assert failed.status is WorkflowRunStatus.RETRYABLE
    assert len(proposer.states) == 1
    assert dispatch.effect_commits == Counter({"call-a": 1})
    assert any(
        [
            call["stable_call_id"]
            for call in checkpoint["state"]
            .get("state", {})
            .get("values", {})
            .get("proposal_outcome", {})
            .get("prepared_calls", [])
        ]
        == ["call-a", "call-b"]
        for checkpoint in history
    )

    restarted_registry = WorkflowRegistry()
    register_v1_workflows(restarted_registry)
    restarted = WorkflowRunner(
        store,
        FencedAsyncSqliteSaver(store.path),
        restarted_registry,
        owner="after-crash",
    )
    recovered = await restarted.run(run_id, None, context)

    assert recovered.status is WorkflowRunStatus.COMPLETED
    assert [state.iteration for state in proposer.states] == [0, 1]
    assert dispatch.attempts["call-a"] == 2
    assert dispatch.effect_commits == Counter({"call-a": 1, "call-b": 1})
    second_turn_messages = proposer.states[1].messages
    assistant_tool_message = next(
        message
        for message in second_turn_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    tool_messages = [
        message for message in second_turn_messages if message.get("role") == "tool"
    ]
    assert [item["id"] for item in assistant_tool_message["tool_calls"]] == [
        "call-a",
        "call-b",
    ]
    assert [message["tool_call_id"] for message in tool_messages] == [
        "call-a",
        "call-b",
    ]
    assert all(isinstance(message["content"], str) for message in tool_messages)
    assert all(json.loads(message["content"])["status"] == "success" for message in tool_messages)
    assert set(recovered.output["values"]) == {"delivery_intents"}
    assert {intent["kind"] for intent in recovered.output["values"]["delivery_intents"]} == {
        "workflow_report",
        "final_assistant",
    }
    report = recovered.output["values"]["delivery_intents"][0]
    assert report["payload"]["status"] == "completed"
    started = {node for node, transition in progress.events if transition == "started"}
    assert {
        "intake",
        "clarify",
        "plan",
        "wait_approval",
        "llm_proposal",
        "tool_execution",
        "test",
        "audit",
        "finalize",
    }.issubset(started)


@pytest.mark.asyncio
async def test_approval_interrupt_survives_executable_restart() -> None:
    proposer = FakeProposer([_outcome(_prepared("approved-call")), _outcome(content="Approved work done")])
    dispatch = JournaledDispatch()
    saver = MemoryNativeStore()
    context = _context(proposer, dispatch, FakeEvaluator())
    state = _state("approval-run", approval_required=True)
    first = CODE_COMPLEX_V1.bind(checkpointer=saver)

    waiting = await first.ainvoke(
        state,
        context,
        thread_id="approval-run",
        run_id="approval-run",
    )

    assert len(proposer.states) == 0
    interrupt_id = _interrupt_id(waiting)
    restarted = CODE_COMPLEX_V1.bind(checkpointer=saver)
    completed = await restarted.resume(
        {interrupt_id: {"approved": True, "action": "approve"}},
        context,
        thread_id="approval-run",
        run_id="approval-run",
    )

    assert len(proposer.states) == 2
    assert completed["values"]["delivery_intents"][0]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_unknown_dynamic_tool_requires_durable_allow_once_decision() -> None:
    dynamic = _prepared("dynamic-call", tool_name="plugin:writer")
    proposer = FakeProposer(
        [
            _outcome(dynamic, source="plugin", access="write"),
            _outcome(content="Dynamic work done"),
        ]
    )
    dispatch = JournaledDispatch()
    saver = MemoryNativeStore()
    context = _context(proposer, dispatch, FakeEvaluator())
    executable = CODE_COMPLEX_V1.bind(checkpointer=saver)

    waiting = await executable.ainvoke(
        _state("dynamic-run", capability_snapshot=[]),
        context,
        thread_id="dynamic-run",
        run_id="dynamic-run",
    )

    interrupt_id = _interrupt_id(waiting)
    assert len(proposer.states) == 1
    completed = await executable.resume(
        {interrupt_id: {"action": "allow_once_opaque"}},
        context,
        thread_id="dynamic-run",
        run_id="dynamic-run",
    )

    assert dispatch.authorizations[0] == {
        "dynamic-call": {"action": "allow_once_opaque", "effect_type": "opaque_manual"}
    }
    assert completed["values"]["delivery_intents"][0]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_process_local_control_tools_are_reported_but_never_dispatched(tmp_path) -> None:
    proposer = FakeProposer(
        [
            _outcome(
                _prepared("todo-call", tool_name="todo_write"),
                _prepared("subagent-call", tool_name="spawn_subagents"),
            ),
            _outcome(_prepared("durable-call")),
            _outcome(content="Durable work completed without control tools"),
        ]
    )
    dispatch = JournaledDispatch()
    runner, _, run_id = await _runner(tmp_path, owner="forbidden-tool-runner")

    result = await runner.run(
        run_id,
        _state(run_id),
        _context(proposer, dispatch, FakeEvaluator()),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    assert set(dispatch.attempts) == {"durable-call"}
    assert dispatch.effect_commits == Counter({"durable-call": 1})


@pytest.mark.asyncio
async def test_proposal_and_fix_budgets_persist_and_incomplete_todo_blocks_completion(
    tmp_path,
) -> None:
    proposer = FakeProposer([_outcome(content="Not done"), _outcome(content="Still not done")])
    dispatch = JournaledDispatch()
    runner, _, run_id = await _runner(tmp_path, owner="budget-runner")

    result = await runner.run(
        run_id,
        _state(run_id, proposal_budget=2, fix_budget=3),
        _context(proposer, dispatch),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    assert len(proposer.states) == 2
    assert result.output["loop_counters"] == {"proposal_turns": 2, "fix_rounds": 0}
    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "blocked"
    assert report["audit"]["reason"] == "incomplete_todos"
    assert report["todos"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_tool_free_read_only_end_turn_completes_active_todo(tmp_path) -> None:
    request = "CTX-ENTRY-CODE：只回复 marker 和本入口 purpose，不调用写工具"
    proposer = FakeProposer(
        [_outcome(content=request, stop_reason="end_turn")]
    )
    runner, _, run_id = await _runner(tmp_path, owner="tool-free-read-only")

    result = await runner.run(
        run_id,
        _state(run_id, request=request, plan_steps=[request]),
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    assert len(proposer.states) == 1
    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "completed"
    assert report["todos"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_write_request_end_turn_without_tool_remains_incomplete(tmp_path) -> None:
    proposer = FakeProposer(
        [_outcome(content="I did not perform the change", stop_reason="end_turn")]
    )
    runner, _, run_id = await _runner(tmp_path, owner="write-no-tool")

    result = await runner.run(
        run_id,
        _state(run_id, proposal_budget=1),
        _context(proposer, JournaledDispatch()),
    )

    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "blocked"
    assert report["audit"]["reason"] == "incomplete_todos"
    assert report["todos"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_failed_audit_uses_one_bounded_fix_round_then_blocks(tmp_path) -> None:
    proposer = FakeProposer(
        [
            _outcome(_prepared("initial-effect")),
            _outcome(content="Initial implementation ready"),
            _outcome(_prepared("fix-effect")),
            _outcome(content="Fix implementation ready"),
        ]
    )
    dispatch = JournaledDispatch()
    evaluator = FakeEvaluator(
        [
            {"passed": False, "evidence_refs": ["test:first-failure"]},
            {"passed": False, "evidence_refs": ["test:second-failure"]},
        ]
    )
    runner, _, run_id = await _runner(tmp_path, owner="fix-budget-runner")

    result = await runner.run(
        run_id,
        _state(run_id, proposal_budget=20, fix_budget=1),
        _context(proposer, dispatch, evaluator),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    assert len(proposer.states) == 4
    assert result.output["loop_counters"] == {"proposal_turns": 4, "fix_rounds": 1}
    assert dispatch.effect_commits == Counter({"initial-effect": 1, "fix-effect": 1})
    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "blocked"
    assert report["audit"]["budget_exhausted"] is True
    assert report["audit"]["reason"] == "tests_failed"


def test_proposal_contract_round_trips_strict_json_and_graph_manifest_is_stable() -> None:
    state = ProposalStateV1(
        messages=[{"role": "user", "content": "Implement it", "message_id": "m1"}],
        original_request="Implement it",
        request_id="request",
        turn_id="turn",
        system_prompt_ref="blob:system",
        prompt_ref="prompt:v1",
        skill_refs=["skill:code"],
        compaction_summary="summary",
        compaction_ref="blob:summary",
        token_estimate=42,
        iteration=1,
        proposal_turns_used=1,
        fix_rounds_used=0,
        tools_used=0,
        active_plan_id="plan-1",
        active_step_id="step-1",
        active_todo_ids=["step-1"],
        tool_signature_repeat_window=["sig-1"],
        completion_attempts=0,
        verify_attempts=0,
        self_check_attempts=0,
        completion_outcomes=[],
        verify_outcomes=[],
        self_check_outcomes=[],
        evidence_refs=[],
        provider_snapshot={"provider": "fake"},
        model_snapshot={"model": "fake"},
        fallback_attempts=[],
        last_error=None,
        pending_tool_results={},
        committed_tool_results={},
        gate_config=GateConfigV1(),
        gate_state=GateStateV1(started_at=123.0),
        convergence=ConvergenceStateV1(),
    )

    payload = state.to_dict()

    assert ProposalStateV1.from_dict(json.loads(json.dumps(payload))).to_dict() == payload
    assert [node.node_id for node in CODE_COMPLEX_V1_DEFINITION.nodes] == [
        "intake",
        "clarify",
        "plan",
        "wait_approval",
        "llm_proposal",
        "tool_execution",
        "completion_decision",
        "test",
        "audit",
        "finalize",
    ]
    assert CODE_COMPLEX_V1_DEFINITION.loop_budgets == {
        "proposal_turns": 20,
        "fix_rounds": 3,
    }
    assert CODE_COMPLEX_V1_DEFINITION.recursion_limit == 128
    assert compile_workflow(CODE_COMPLEX_V1_DEFINITION).manifest.to_dict() == CODE_COMPLEX_V1.manifest.to_dict()
