from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import OutcomeStatus, RunContext
from deskpet.execution.failure_reports import FailureReportIssuer
from deskpet.harness.drivers.react import (
    AgentLoopCollaborator,
    ReActDriver,
    ReactCommandBoundary,
    ReactToolBatch,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall


def _call(call_id: str = "call-1") -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="process_start",
        stable_call_id=call_id,
        final_params={"executable": "missing.exe", "argv": []},
        prepared_targets=(),
        tool_spec_version="v1",
        schema_hash="schema-v1",
        permission_policy_version="policy-v1",
        effect_type="opaque_manual",
        effect_policy_version="v1",
    )


def _context(call_id: str = "call-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope-1",
        session_id="session-1",
        request_id="request-1",
        root_run_id="root-1",
        turn_id="turn-1",
        venue="text",
        workspace="F:/workspace/task-1",
        write_scope_root="F:/workspace/task-1",
        capability_hash="c" * 64,
        scope_hash="d" * 64,
        provider_plan=("fixture",),
        run_id="root-1",
        call_id=call_id,
        effect_id=f"effect-{call_id}",
        trace_id="trace-1",
    )


def _boundary() -> ReactCommandBoundary:
    call = _call()
    context = _context()
    return ReactCommandBoundary(
        run_id="root-1",
        session_id="session-1",
        command_id="batch-1",
        command_kind="execute_tools",
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": call.stable_call_id,
                        "type": "function",
                        "function": {
                            "name": call.tool_name,
                            "arguments": "{}",
                        },
                    }
                ],
            },
        ),
        session_projection_cursor=0,
        prepared_context_ref="checkpoint-1",
        tool_set_snapshot_ref="tools-1",
        pending_calls=(call,),
        tool_contexts=(context,),
        outcomes=(
            NormalizedToolOutcome.failure(
                "executable_not_found", "missing.exe was not found"
            ),
        ),
        outcome_statuses=(OutcomeStatus.FAILED,),
        provider_state={},
        iteration=1,
        completion_state={
            "plan_version": 1,
            "current_attempt": {
                "attempt_id": "attempt-1",
                "provider_turn_id": "batch-1",
                "provider_batch_id": "batch-1",
                "plan_version": 1,
                "strategy_fingerprint": "strategy-1",
                "planned_call_refs": ["call-1"],
                "status": "running",
                "budget_eligible": True,
            },
        },
        request_payload={"task_scope_id": "task-1"},
        run_context=RunContext(
            session_id="session-1",
            root_run_id="root-1",
            parent_run_id=None,
            request_id="request-1",
            turn_id="turn-1",
            venue="text",
            workspace={"root": "F:/workspace/task-1"},
            capability_hash="c" * 64,
            provider_plan={"providers": ["fixture"]},
            trace_id="trace-1",
            principal_id="local:session-1",
        ),
    )


def test_failed_tool_backfills_trusted_report_then_advances_plan_version() -> None:
    staged = ReActDriver._stage_failure_reports(_boundary())
    metadata = dict(staged.outcome_metadata[0])
    report = dict(metadata["failure_report"])

    assert report["source_kind"] == "tool_executor"
    assert report["error_code"] == "executable_not_found"
    assert report["task_scope_id"] == "task-1"
    assert staged.completion_state["attempt_status"] == "failed"
    assert staged.completion_state["latest_failure_set_id"]

    tool_message = ReActDriver._tool_messages(staged)[-1]
    body = json.loads(str(tool_message["content"]))
    assert body["failure_report_ref"] == report["report_ref"]
    assert body["failure_report"]["error_code"] == "executable_not_found"

    class Registry:
        @staticmethod
        def prepared_execution_policy(_call):
            return False, False

    driver = ReActDriver(object(), object(), Registry())
    next_call = _call("call-2")
    next_boundary = driver._boundary_for_batch(
        staged.to_start(),
        ReactToolBatch(
            "batch-2",
            (next_call,),
            (_context("call-2"),),
            staged.canonical_messages,
            2,
        ),
        staged.version,
    )
    assert next_boundary.completion_state["plan_version"] == 2
    assert next_boundary.completion_state["attempt_status"] == "running"


def test_raw_failure_backfill_preserves_recovery_directives() -> None:
    raw = AgentLoopCollaborator._raw_failure(
        run_id="run-react",
        tool_call=SimpleNamespace(
            id="unexposed-1",
            name="run_shell",
            arguments={"command": "echo ok"},
            args_raw=None,
            args_parse_error=None,
        ),
        call_order=0,
        source_kind="tool_unknown",
        error_code="tool_not_exposed",
        message="discover and activate the capability",
        retriable=True,
        replan=True,
    )
    boundary = replace(
        _boundary(),
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        outcome_statuses=(),
        outcome_metadata=(),
        raw_failures=(raw,),
        provider_call_order=("unexposed-1",),
    )

    tool_message = ReActDriver._tool_messages(boundary)[-1]
    body = json.loads(str(tool_message["content"]))

    assert body["admission"]["retriable"] is True
    assert body["admission"]["replan"] is True


def test_repeated_current_strategy_is_deduplicated_before_durable_report() -> None:
    boundary = _boundary()
    strategy = "a" * 64
    current = {
        **dict(boundary.completion_state["current_attempt"]),
        "strategy_fingerprint": strategy,
    }
    boundary = replace(
        boundary,
        completion_state={
            **dict(boundary.completion_state),
            "current_attempt": current,
            "prior_strategy_fingerprints": [strategy],
        },
    )

    staged = ReActDriver._stage_failure_reports(boundary)
    report = staged.completion_state["failure_reports"][0]

    assert report["prior_strategy_fingerprints"] == [strategy]
    # This is the exact conversion that previously crashed the live recovery
    # path after a provider tool-batch overflow.
    from deskpet.execution.contracts import TaskFailureReport

    TaskFailureReport(**report)


@pytest.mark.asyncio
async def test_successful_replan_batch_does_not_repersist_prior_failure_set() -> None:
    class Registry:
        @staticmethod
        def prepared_execution_policy(_call):
            return False, False

    driver = ReActDriver(object(), object(), Registry())
    staged = ReActDriver._stage_failure_reports(_boundary())
    next_call = _call("call-2")
    next_boundary = driver._boundary_for_batch(
        staged.to_start(),
        ReactToolBatch(
            "batch-2",
            (next_call,),
            (_context("call-2"),),
            staged.canonical_messages,
            2,
        ),
        staged.version,
    ).with_outcomes(
        {
            0: NormalizedToolOutcome.success(
                {"replanned": True, "result": "completed"}
            )
        },
        {0: OutcomeStatus.SUCCEEDED},
    )
    completed = ReActDriver._stage_failure_reports(next_boundary)

    persisted = await driver._persist_failure_set(completed)

    assert persisted is completed
    assert persisted.completion_state["attempt_status"] == "succeeded"
    assert (
        persisted.completion_state["failure_report_command_id"]
        == "batch-1"
    )
    assert persisted.command_id == "batch-2"


def test_unchanged_failed_action_is_rejected_before_dispatch() -> None:
    class Registry:
        @staticmethod
        def prepared_execution_policy(_call):
            return False, False

    driver = ReActDriver(object(), object(), Registry())
    first_call = _call("call-first")
    first = driver._boundary_for_batch(
        _boundary().to_start(),
        ReactToolBatch(
            "batch-first",
            (first_call,),
            (_context("call-first"),),
            _boundary().canonical_messages,
            1,
        ),
    ).with_outcomes(
        {
            0: NormalizedToolOutcome.failure(
                "executable_not_found", "missing.exe was not found"
            )
        },
        {0: OutcomeStatus.FAILED},
    )
    failed = ReActDriver._stage_failure_reports(first)

    repeated_call = _call("call-repeated")
    repeated = driver._boundary_for_batch(
        failed.to_start(),
        ReactToolBatch(
            "batch-repeated",
            (repeated_call,),
            (_context("call-repeated"),),
            failed.canonical_messages,
            2,
        ),
        failed.version,
    )

    assert repeated.pending_indexes == ()
    assert repeated.outcomes[0] is not None
    assert repeated.outcomes[0].error["code"] == "replan_required"
    assert repeated.completion_state["attempt_status"] == "rejected"
    assert (
        repeated.completion_state["loop_guard_decision"]
        == "reject_same_strategy"
    )


def test_three_distinct_same_cause_strategies_create_honest_blocker() -> None:
    class Registry:
        @staticmethod
        def prepared_execution_policy(_call):
            return False, False

    driver = ReActDriver(object(), object(), Registry())
    call = _call("call-budget")
    action = driver._prepared_action_fingerprint(call)
    report = FailureReportIssuer.issue(
        root_run_id="root-1",
        run_id="root-1",
        task_scope_id="task-1",
        attempt_id="attempt-prior",
        plan_version=3,
        call_record_id="provider-call:prior",
        source_kind="tool_executor",
        source_identity="process_start@v1",
        provider_call_id="call-prior",
        failed_call_id="call-prior",
        failed_step="process_start",
        error_code="executable_not_found",
        action_fingerprint=action,
        prior_strategy_fingerprints=(
            "strategy-a",
            "strategy-b",
            "strategy-c",
        ),
    )
    request = _boundary().to_start()
    request = replace(
        request,
        completion_state={
            **dict(request.completion_state),
            "failure_reports": [report.to_dict()],
            "latest_failure_set_id": "failure-set:prior",
            "attempt_status": "failed",
            "plan_version": 3,
            "prior_strategy_fingerprints": [
                "strategy-a",
                "strategy-b",
                "strategy-c",
            ],
        },
    )

    blocked = driver._boundary_for_batch(
        request,
        ReactToolBatch(
            "batch-budget",
            (call,),
            (_context("call-budget"),),
            request.canonical_messages,
            4,
        ),
    )

    assert blocked.pending_indexes == ()
    assert blocked.completion_state["attempt_status"] == "blocked"
    assert blocked.completion_state["agent_loop_terminal_error"]
    assert blocked.outcomes[0].error["code"] == "attempt_budget_exhausted"
