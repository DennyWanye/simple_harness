from __future__ import annotations

import json
from collections import Counter

import httpx
import pytest

from llm.errors import LLMProviderError
from deskpet.execution.provider_invocations import ProviderDispatchUnknownError
from deskpet.workflows import WorkflowContext, WorkflowRunStatus, compile_workflow
from deskpet.workflows.definitions.code_nodes import (
    CapabilitySnapshotV1,
    TaskSessionRefV1,
    WorkflowSessionRefV1,
    _provider_failure_is_retryable,
    _provider_failure_message_ref,
)
from deskpet.workflows.definitions.v1 import (
    CODE_COMPLEX_V1,
    CODE_COMPLEX_V1_DEFINITION,
    code_complex_initial_state,
    durable_task_initial_state,
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
    ProposalErrorV1,
    ProposalOutcomeV1,
    ProposalStateV1,
)
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore
from tests.test_workflow_native_engine import MemoryNativeStore
from deskpet.memory.session_db import SessionDB


def test_provider_transport_timeout_is_retryable_but_402_is_not() -> None:
    timeout = ProviderDispatchUnknownError(
        "provider_dispatch_unknown_after_handoff"
    )
    timeout.__cause__ = httpx.ReadTimeout("stream stalled")
    assert _provider_failure_is_retryable(timeout) is True

    request = httpx.Request("POST", "https://relay.invalid/v1/chat")
    response = httpx.Response(402, request=request)
    balance_error = ProviderDispatchUnknownError(
        "provider_dispatch_unknown_after_handoff"
    )
    balance_error.__cause__ = httpx.HTTPStatusError(
        "status_code=402",
        request=request,
        response=response,
    )
    assert _provider_failure_is_retryable(balance_error) is False


def test_provider_failure_message_ref_preserves_safe_balance_diagnostic() -> None:
    balance_error = LLMProviderError(
        "raw provider detail must not cross the checkpoint",
        status_code=402,
        error_class="insufficient_balance",
    )

    assert _provider_failure_message_ref(balance_error) == (
        "provider:insufficient_balance"
    )


@pytest.mark.parametrize(
    "code",
    [
        "tool_activation_revision_conflict",
        "tool_activation_scope_conflict",
    ],
)
def test_provider_failure_message_ref_preserves_safe_runtime_code(code: str) -> None:
    assert _provider_failure_message_ref(RuntimeError(code)) == (
        f"workflow_node:llm_proposal:{code}"
    )


def test_llm_proposal_node_retries_transient_provider_failures() -> None:
    node = next(
        item
        for item in CODE_COMPLEX_V1_DEFINITION.nodes
        if item.node_id == "llm_proposal"
    )
    assert node.retry_policy.max_attempts == 3
    assert node.retry_policy.retryable_codes == frozenset(
        {"retryable_provider"}
    )


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
    capability_admission: str | None = None,
    stop_reason: str | None = None,
    compacted_messages: list[dict[str, object]] | None = None,
    compaction_summary: str | None = None,
    compaction_ref: str | None = None,
    token_estimate: int = 0,
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
                **(
                    {"capability_admission": capability_admission}
                    if capability_admission
                    else {}
                ),
            }
            for call in calls
        ],
        prepared_calls=list(calls),
        stop_reason=stop_reason or ("tool_calls" if calls else "stop"),
        usage={"input_tokens": 20, "output_tokens": 10},
        provider="fake-provider",
        model="fake-model",
        compacted_messages=compacted_messages,
        compaction_summary=compaction_summary,
        compaction_ref=compaction_ref,
        token_estimate=token_estimate,
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


def _context(
    proposer,
    dispatch,
    evaluator=None,
    progress=None,
    output_contract=None,
) -> WorkflowContext:
    ports = {"llm": proposer, "tool": dispatch}
    if evaluator is not None:
        ports["evaluator"] = evaluator
    if progress is not None:
        ports["progress"] = progress
    if output_contract is not None:
        ports["output_contract"] = output_contract
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


async def _durable_runner(tmp_path, *, owner: str):
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
        workflow_name="durable_task",
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


def _durable_state(run_id: str, **overrides):
    values = {
        "request": "Implement the durable task",
        "run_id": run_id,
        "session_ref": TaskSessionRefV1(
            session_id="base-session",
            task_scope_id=f"task:{run_id}",
            delivery_session_id="delivery-session",
            workspace_hash="workspace-hash",
            session_epoch=0,
            task_epoch=0,
        ),
        "capability_snapshot": [_capability()],
        "approval_required": False,
        "plan_steps": ["Implement and verify the task"],
    }
    values.update(overrides)
    return durable_task_initial_state(**values)


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
    assert sum(
        1
        for message in second_turn_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    ) == 1
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
async def test_approved_plan_does_not_ask_for_approval_again() -> None:
    proposer = FakeProposer(
        [
            _outcome(_prepared("inspect-call")),
            _outcome(_prepared("write-call")),
            _outcome(content="Approved work done"),
        ]
    )
    saver = MemoryNativeStore()
    executable = CODE_COMPLEX_V1.bind(checkpointer=saver)
    state = _state(
        "approval-step-run",
        approval_required=True,
        plan_steps=["Inspect files", "等待批准", "Apply the approved fix"],
    )

    waiting = await executable.ainvoke(
        state,
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
        thread_id="approval-step-run",
        run_id="approval-step-run",
    )
    completed = await executable.resume(
        {_interrupt_id(waiting): {"approved": True, "action": "approve"}},
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
        thread_id="approval-step-run",
        run_id="approval-step-run",
    )

    assert any(
        message.get("message_id") == "workflow-plan-approved"
        for message in proposer.states[0].messages
    )
    report = completed["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "completed"
    assert [item["status"] for item in report["todos"]] == [
        "completed",
        "completed",
        "completed",
    ]


@pytest.mark.asyncio
async def test_durable_task_prioritizes_execution_over_repeated_discovery() -> None:
    proposer = FakeProposer(
        [
            _outcome(_prepared("write-call")),
            _outcome(content="Durable work done", stop_reason="end_turn"),
        ]
    )
    executable = CODE_COMPLEX_V1.bind(checkpointer=MemoryNativeStore())
    completed = await executable.ainvoke(
        _durable_state("durable-execution-discipline"),
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
        thread_id="durable-execution-discipline",
        run_id="durable-execution-discipline",
    )

    discipline = next(
        message
        for message in proposer.states[0].messages
        if message.get("message_id") == "durable-task-execution-discipline"
    )
    assert "exact capability_id" in discipline["content"]
    assert "do not repeat semantically equivalent searches" in discipline["content"]
    assert completed["values"]["delivery_intents"][0]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_compacted_workflow_context_is_checkpointed_before_next_proposal(
    tmp_path,
) -> None:
    compacted = [
        {"role": "system", "content": "[压缩摘要] 已确认工作区与当前任务"},
        {"role": "user", "content": "Implement the durable code change"},
    ]
    proposer = FakeProposer(
        [
            _outcome(
                _prepared("compacted-call"),
                compacted_messages=compacted,
                compaction_summary="已确认工作区与当前任务",
                compaction_ref="workflow-compaction:sha256:test",
                token_estimate=18,
            ),
            _outcome(content="Implementation complete", token_estimate=24),
        ]
    )
    runner, _, run_id = await _runner(tmp_path, owner="compaction-checkpoint")

    result = await runner.run(
        run_id,
        _state(run_id),
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    assert len(proposer.states) == 2
    resumed = proposer.states[1]
    assert resumed.messages[0]["content"].startswith("[压缩摘要]")
    assert resumed.compaction_ref == "workflow-compaction:sha256:test"
    assert resumed.compaction_summary == "已确认工作区与当前任务"
    assert resumed.token_estimate == 18
    assert any(message["role"] == "tool" for message in resumed.messages)


@pytest.mark.asyncio
async def test_durable_task_compacts_and_stops_repeated_discovery_searches() -> None:
    searches = [
        _prepared(f"search-{index}", tool_name="capability_search")
        for index in range(4)
    ]
    proposer = FakeProposer(
        [
            *[_outcome(call, access="read") for call in searches],
            _outcome(_prepared("write-call")),
            _outcome(content="Durable work done", stop_reason="end_turn"),
        ]
    )
    dispatch = JournaledDispatch()
    completed = await CODE_COMPLEX_V1.bind(
        checkpointer=MemoryNativeStore()
    ).ainvoke(
        _durable_state(
            "durable-discovery-limit",
            capability_snapshot=[
                _capability("capability_search"),
                _capability("write_file"),
            ],
            proposal_budget=8,
        ),
        _context(proposer, dispatch, FakeEvaluator()),
        thread_id="durable-discovery-limit",
        run_id="durable-discovery-limit",
    )

    assert dispatch.attempts["search-0"] == 1
    assert dispatch.attempts["search-1"] == 1
    assert dispatch.attempts["search-2"] == 1
    assert dispatch.attempts["search-3"] == 0
    blocked_messages = [
        message
        for message in proposer.states[4].messages
        if message.get("role") == "tool"
        and "discovery_loop_blocked" in str(message.get("content"))
    ]
    assert len(blocked_messages) == 1
    prior_search_results = [
        message
        for message in proposer.states[3].messages
        if message.get("role") == "tool"
        and message.get("name") == "capability_search"
    ]
    assert len(prior_search_results) == 1
    assert completed["values"]["delivery_intents"][0]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_durable_task_blocks_repeated_failed_capability_describes() -> None:
    describes = [
        _prepared(f"describe-{index}", tool_name="tool_describe")
        for index in range(3)
    ]
    write = _prepared("write-after-replan")
    proposer = FakeProposer(
        [
            *[_outcome(call, access="read") for call in describes],
            _outcome(write),
            _outcome(content="Durable work done", stop_reason="end_turn"),
        ]
    )

    class FailingDescribeDispatch(JournaledDispatch):
        async def dispatch(
            self,
            prepared_calls,
            *,
            workflow_step_id,
            prior_results,
            authorizations,
        ):
            if all(call.tool_name == "tool_describe" for call in prepared_calls):
                self.authorizations.append(dict(authorizations))
                results = {}
                for call in prepared_calls:
                    self.attempts[call.stable_call_id] += 1
                    results[call.stable_call_id] = {
                        "ok": False,
                        "status": "failed",
                        "error": "capability_denied",
                        "step_seen": workflow_step_id,
                    }
                return results
            return await super().dispatch(
                prepared_calls,
                workflow_step_id=workflow_step_id,
                prior_results=prior_results,
                authorizations=authorizations,
            )

    dispatch = FailingDescribeDispatch()
    completed = await CODE_COMPLEX_V1.bind(
        checkpointer=MemoryNativeStore()
    ).ainvoke(
        _durable_state(
            "durable-describe-limit",
            capability_snapshot=[
                _capability("tool_describe"),
                _capability("tool_search"),
                _capability("write_file"),
            ],
            proposal_budget=8,
        ),
        _context(proposer, dispatch, FakeEvaluator()),
        thread_id="durable-describe-limit",
        run_id="durable-describe-limit",
    )

    assert dispatch.attempts["describe-0"] == 1
    assert dispatch.attempts["describe-1"] == 1
    assert dispatch.attempts["describe-2"] == 0
    blocked_messages = [
        message
        for message in proposer.states[3].messages
        if message.get("role") == "tool"
        and "capability_describe_loop_blocked" in str(message.get("content"))
    ]
    assert len(blocked_messages) == 1
    assert completed["values"]["delivery_intents"][0]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_approved_plan_authorizes_frozen_non_read_effect_once() -> None:
    call = _prepared("shell-call", tool_name="run_shell")
    capability = CapabilitySnapshotV1(
        tool_name="run_shell",
        source="builtin",
        schema_hash="schema-run_shell",
        spec_version="1",
        effect_policy={"kind": "opaque_manual", "version": "1"},
        lifecycle_hash="lifecycle-v1",
        outcome_parser_hash="outcome-v1",
    )
    proposer = FakeProposer(
        [_outcome(call), _outcome(content="pytest passed", stop_reason="end_turn")]
    )
    dispatch = JournaledDispatch()
    saver = MemoryNativeStore()
    executable = CODE_COMPLEX_V1.bind(checkpointer=saver)

    waiting = await executable.ainvoke(
        _state(
            "approved-shell-run",
            approval_required=True,
            request="Run pytest",
            capability_snapshot=[capability],
            plan_steps=["Run pytest"],
        ),
        _context(proposer, dispatch, FakeEvaluator()),
        thread_id="approved-shell-run",
        run_id="approved-shell-run",
    )
    completed = await executable.resume(
        {_interrupt_id(waiting): {"approved": True, "action": "approve"}},
        _context(proposer, dispatch, FakeEvaluator()),
        thread_id="approved-shell-run",
        run_id="approved-shell-run",
    )

    assert completed["values"]["delivery_intents"][0]["payload"]["status"] == "completed"
    assert dispatch.authorizations[0] == {
        "shell-call": {"action": "allow_once_opaque"}
    }


@pytest.mark.asyncio
async def test_approved_plan_dispatches_context_os_activated_write() -> None:
    call = _prepared("activated-write", tool_name="file_write")
    proposer = FakeProposer(
        [
            _outcome(
                call,
                capability_admission="context_os_activated",
            ),
            _outcome(content="Created and verified", stop_reason="end_turn"),
        ]
    )
    dispatch = JournaledDispatch()
    executable = CODE_COMPLEX_V1.bind(checkpointer=MemoryNativeStore())

    completed = await executable.ainvoke(
        _state(
            "activated-write-run",
            capability_snapshot=[],
            request="Create the project files",
            plan_steps=["Create the project files"],
        ),
        _context(proposer, dispatch, FakeEvaluator()),
        thread_id="activated-write-run",
        run_id="activated-write-run",
    )

    assert completed["values"]["delivery_intents"][0]["payload"]["status"] == "completed"
    assert dispatch.attempts == Counter({"activated-write": 1})
    assert dispatch.authorizations[0] == {
        "activated-write": {"action": "allow_once_opaque"}
    }


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
async def test_continue_without_records_dynamic_tool_as_non_retryable() -> None:
    dynamic = _prepared("dynamic-call", tool_name="mcp_filesystem_list_directory")
    proposer = FakeProposer(
        [
            _outcome(dynamic, source="mcp", access="write"),
            _outcome(
                _prepared("dynamic-call-2", tool_name="mcp_filesystem_list_directory"),
                source="mcp",
                access="write",
            ),
        ]
    )
    saver = MemoryNativeStore()
    context = _context(proposer, JournaledDispatch(), FakeEvaluator())
    executable = CODE_COMPLEX_V1.bind(checkpointer=saver)

    waiting = await executable.ainvoke(
        _state("dynamic-decline-run", capability_snapshot=[]),
        context,
        thread_id="dynamic-decline-run",
        run_id="dynamic-decline-run",
    )
    waiting_again = await executable.resume(
        {_interrupt_id(waiting): {"action": "continue_without"}},
        context,
        thread_id="dynamic-decline-run",
        run_id="dynamic-decline-run",
    )

    assert _interrupt_id(waiting_again)
    result = saver.snapshot.state["values"]["proposal_state"]["committed_tool_results"][
        "dynamic-call"
    ]
    assert result == {
        "stable_call_id": "dynamic-call",
        "tool_name": "mcp_filesystem_list_directory",
        "status": "failed",
        "code": "unsupported_dynamic_tool",
        "retryable": False,
    }


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
async def test_terminal_answer_requires_write_and_test_receipts(tmp_path) -> None:
    write_call = _prepared("write-call", tool_name="write_file")
    test_call = _prepared("test-call", tool_name="run_shell")
    proposer = FakeProposer(
        [
            _outcome(write_call),
            _outcome(test_call),
            _outcome(content="Fixed the code; pytest passed.", stop_reason="end_turn"),
        ]
    )
    runner, _, run_id = await _runner(tmp_path, owner="write-test-terminal")

    result = await runner.run(
        run_id,
        _state(
            run_id,
            request="Fix calculator.py and run pytest",
            capability_snapshot=[_capability("write_file"), _capability("run_shell")],
            plan_steps=["Inspect", "Apply fix", "Run pytest", "Report result"],
        ),
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
    )

    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "completed"
    assert [item["status"] for item in report["todos"]] == ["completed"] * 4
    tool_messages = [
        message
        for state in proposer.states
        for message in state.messages
        if message.get("role") == "tool"
    ]
    assert {json.loads(message["content"])["tool_name"] for message in tool_messages} == {
        "write_file",
        "run_shell",
    }


@pytest.mark.asyncio
async def test_durable_task_discovery_receipts_cannot_complete_effectful_todo(
    tmp_path,
) -> None:
    proposer = FakeProposer(
        [
            _outcome(_prepared("workspace", tool_name="workspace_prepare")),
            _outcome(_prepared("memory", tool_name="memory_search")),
            _outcome(content="The requested fix is complete.", stop_reason="end_turn"),
        ]
    )
    runner, _, run_id = await _durable_runner(
        tmp_path,
        owner="durable-discovery-only",
    )

    result = await runner.run(
        run_id,
        _durable_state(
            run_id,
            request="复制并修复 Godot 项目，然后启动验证",
            capability_snapshot=[
                _capability("workspace_prepare"),
                _capability("memory_search"),
            ],
            proposal_budget=3,
            fix_budget=0,
        ),
        _context(proposer, JournaledDispatch()),
    )

    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "blocked"
    assert report["audit"]["reason"] == "incomplete_todos"
    assert report["todos"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_durable_task_explicit_tool_free_end_turn_completes_once(
    tmp_path,
) -> None:
    request = "不要调用任何工具，仅返回 A_OK。"
    proposer = FakeProposer(
        [_outcome(content="A_OK", stop_reason="end_turn")]
    )
    runner, _, run_id = await _durable_runner(
        tmp_path,
        owner="durable-tool-free-terminal",
    )

    result = await runner.run(
        run_id,
        _durable_state(
            run_id,
            request=request,
            plan_steps=[request],
            proposal_budget=3,
            fix_budget=0,
        ),
        _context(proposer, JournaledDispatch()),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    assert len(proposer.states) == 1
    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "completed"
    assert report["todos"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_durable_task_output_contract_failure_blocks_completion(
    tmp_path,
) -> None:
    class FailedOutputContract:
        async def audit(self):
            return {
                "passed": False,
                "missing_outputs": ["result.json"],
                "retained_scratch": [],
                "baseline_matches": True,
            }

    request = "不要调用任何工具，仅返回 A_OK。"
    proposer = FakeProposer(
        [_outcome(content="A_OK", stop_reason="end_turn")]
    )
    runner, _, run_id = await _durable_runner(
        tmp_path,
        owner="durable-output-contract-failure",
    )

    result = await runner.run(
        run_id,
        _durable_state(
            run_id,
            request=request,
            plan_steps=[request],
            proposal_budget=2,
            fix_budget=0,
        ),
        _context(
            proposer,
            JournaledDispatch(),
            output_contract=FailedOutputContract(),
        ),
    )

    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "blocked"
    assert report["audit"]["reason"] == "tests_failed"
    assert report["audit"]["failure_code"] == "task_output_contract_failed"
    assert report["audit"]["output_contract"]["missing_outputs"] == [
        "result.json"
    ]


@pytest.mark.asyncio
async def test_durable_task_accepts_production_tool_outcome_state_as_receipt(
    tmp_path,
) -> None:
    class ProductionOutcomeDispatch(JournaledDispatch):
        async def dispatch(
            self,
            prepared_calls,
            *,
            workflow_step_id,
            prior_results,
            authorizations,
        ):
            results = await super().dispatch(
                prepared_calls,
                workflow_step_id=workflow_step_id,
                prior_results=prior_results,
                authorizations=authorizations,
            )
            return {
                call_id: {
                    "state": "success",
                    "value": {"content": "# DeskPet"},
                }
                for call_id in results
            }

    proposer = FakeProposer(
        [
            _outcome(_prepared("read-call", tool_name="read_file")),
            _outcome(content="The requested headings were read and reported.", stop_reason="end_turn"),
        ]
    )
    runner, _, run_id = await _durable_runner(
        tmp_path,
        owner="durable-production-read-outcome",
    )

    result = await runner.run(
        run_id,
        _durable_state(
            run_id,
            request=(
                "请用多步骤任务完成只读检查：在 F:\\projects\\deskpet 中读取 "
                "README.md，列出前三个一级标题并报告。必须使用 "
                "workflow.durable_task，全程禁止修改文件。"
            ),
            capability_snapshot=[_capability("read_file")],
            proposal_budget=3,
            fix_budget=0,
        ),
        _context(proposer, ProductionOutcomeDispatch()),
    )

    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "completed"
    assert report["audit"]["test_passed"] is True
    assert report["todos"][0]["status"] == "completed"
    assert len(proposer.states) == 2


@pytest.mark.asyncio
async def test_durable_task_default_audit_requires_distinct_write_and_test_receipts(
    tmp_path,
) -> None:
    proposer = FakeProposer(
        [
            _outcome(_prepared("write-call", tool_name="write_file")),
            _outcome(_prepared("test-call", tool_name="run_shell")),
            _outcome(content="The requested fix and verification are complete.", stop_reason="end_turn"),
        ]
    )
    runner, _, run_id = await _durable_runner(
        tmp_path,
        owner="durable-write-test",
    )

    result = await runner.run(
        run_id,
        _durable_state(
            run_id,
            request="复制并修复 Godot 项目，然后启动验证",
            capability_snapshot=[
                _capability("write_file"),
                _capability("run_shell"),
            ],
            proposal_budget=4,
            fix_budget=0,
        ),
        _context(proposer, JournaledDispatch()),
    )

    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "completed"
    assert report["audit"]["test_passed"] is True
    assert report["todos"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_durable_task_one_shell_receipt_cannot_prove_change_and_verification(
    tmp_path,
) -> None:
    proposer = FakeProposer(
        [
            _outcome(
                _prepared(
                    "unrelated-read-1",
                    tool_name="mcp_filesystem_read_text_file",
                ),
                _prepared(
                    "unrelated-read-2",
                    tool_name="mcp_filesystem_read_text_file",
                ),
            ),
            _outcome(_prepared("copy-call", tool_name="run_shell")),
            _outcome(content="The requested fix and verification are complete.", stop_reason="end_turn"),
        ]
    )
    runner, _, run_id = await _durable_runner(
        tmp_path,
        owner="durable-single-shell",
    )

    result = await runner.run(
        run_id,
        _durable_state(
            run_id,
            request="复制并修复 Godot 项目，然后启动验证",
            capability_snapshot=[_capability("run_shell")],
            proposal_budget=3,
            fix_budget=0,
        ),
        _context(proposer, JournaledDispatch()),
    )

    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "blocked"
    assert report["audit"]["reason"] == "tests_failed"
    assert report["audit"]["test_passed"] is False


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


@pytest.mark.asyncio
async def test_rejected_proposal_error_is_fed_back_to_next_model_turn(
    tmp_path,
) -> None:
    rejected = ProposalOutcomeV1(
        assistant_content="",
        reasoning_summary_ref=None,
        raw_tool_proposals=[
            {
                "stable_call_id": "rejected-shell",
                "tool_name": "run_shell",
                "raw_params": {"command": "echo ready"},
                "source": "builtin",
                "access": "write",
            }
        ],
        prepared_calls=[],
        stop_reason="tool_use",
        usage={"input_tokens": 10, "output_tokens": 4},
        provider="fake-provider",
        model="fake-model",
        error=ProposalErrorV1(
            code="tool_requires_activation",
            message_ref="proposal:rejected-shell:activation_required",
            retryable=True,
            details={
                "tool_name": "run_shell",
                "capability_id": "builtin:run_shell",
                "recovery": [
                    "tool_search",
                    "tool_describe",
                    "tool_activate",
                ],
            },
        ),
    )
    proposer = FakeProposer(
        [
            rejected,
            _outcome(_prepared("implemented")),
            _outcome(content="Implementation complete"),
        ]
    )
    runner, _, run_id = await _runner(tmp_path, owner="proposal-feedback")

    result = await runner.run(
        run_id,
        _state(run_id),
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    assert not any(
        message.get("role") == "assistant"
        and not str(message.get("content") or "")
        and not message.get("tool_calls")
        for message in proposer.states[1].messages
    )
    feedback = proposer.states[1].messages[-1]
    assert feedback["role"] == "system"
    payload = json.loads(feedback["content"])
    assert payload["type"] == "host_tool_proposal_rejected"
    assert payload["error"]["code"] == "tool_requires_activation"
    assert payload["error"]["details"]["recovery"] == [
        "tool_search",
        "tool_describe",
        "tool_activate",
    ]


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
