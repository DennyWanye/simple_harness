from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deskpet.workflows.control import workflow_interrupt as interrupt

from deskpet.workflows.contracts import (
    ChannelSpec,
    JsonType,
    ReducerKind,
    StatePatch,
    WorkflowContext,
    WorkflowRunStatus,
)
from deskpet.workflows.definition import (
    END_NODE,
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)
from deskpet.workflows.human import DecisionStatus, HumanDecisionError, HumanDecisionStore
from deskpet.workflows.ipc import WorkflowIPCDispatcher
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore


async def _approval_node(state, context):
    del state, context
    response = interrupt(
        {
            "kind": "approval",
            "title": "Apply the durable change?",
            "options": ["approve", "reject"],
        }
    )
    return StatePatch({"answer": response})


def _workflow():
    return compile_workflow(
        WorkflowDefinition(
            name="human-integration",
            version="1",
            state_schema_version=1,
            entry_node="approval",
            nodes=(
                NodeDefinition(
                    "approval",
                    _approval_node,
                    interrupt_capable=True,
                    barrier=True,
                    exclusive_superstep=True,
                ),
            ),
            channels={
                "answer": ChannelSpec(
                    JsonType.JSON,
                    ReducerKind.SINGLE_WRITER,
                    frozenset({"approval"}),
                )
            },
            recursion_limit=8,
            max_supersteps=4,
            edges=(Edge("approval", END_NODE),),
        )
    )


async def _waiting_run(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    saver = FencedAsyncSqliteSaver(path)
    registry = WorkflowRegistry()
    registry.register(_workflow())
    runner = WorkflowRunner(store, saver, registry, owner="human-integration-test")
    run_id = await runner.start(
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="human-integration",
        workflow_version="1",
        capability_snapshot={},
    )
    result = await runner.run(
        run_id,
        {
            "schema_version": 1,
            "workflow_name": "human-integration",
            "workflow_version": "1",
            "thread_id": run_id,
            "run_id": run_id,
            "session_id": "session",
        },
        WorkflowContext(),
    )
    return store, runner, run_id, result


@pytest.mark.asyncio
async def test_graph_interrupt_decision_resolve_resumes_once_across_store_restart(tmp_path):
    store, runner, run_id, waiting = await _waiting_run(tmp_path)

    assert waiting.status is WorkflowRunStatus.WAITING
    run = await store.get_run(run_id)
    assert run is not None
    assert run["status"] == "waiting"
    assert run["lease_owner"] is None

    restarted_human_store = HumanDecisionStore(store.path)
    decisions = await restarted_human_store.list_open_decisions(run_id=run_id)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.status is DecisionStatus.OPEN
    assert decision.kind == "approval"
    assert decision.interrupt_id
    assert decision.checkpoint_id == run["head_checkpoint_id"]

    original_resume = runner.resume
    runner.resume = AsyncMock(side_effect=original_resume)
    service = WorkflowService(store, runner, human_store=restarted_human_store)
    dispatcher = WorkflowIPCDispatcher(service)
    response = await dispatcher.dispatch(
        {
            "type": "workflow_decision_resolve",
            "request_id": "resolve-1",
            "payload": {
                "decision_id": decision.decision_id,
                "nonce": decision.nonce,
                "expected_version": decision.version,
                "response": {"approved": True},
            },
        }
    )

    assert response["ok"] is True
    resolved = response["payload"]
    assert resolved["status"] == "resolved"
    assert resolved["resume"]["triggered"] is True
    assert resolved["resume"]["interrupt_ids"] == [decision.interrupt_id]
    assert resolved["resume"]["result"]["status"] == "completed"
    assert resolved["resume"]["result"]["output"]["answer"] == {"approved": True}
    assert runner.resume.await_count == 1
    completed = await store.get_run(run_id)
    assert completed is not None and completed["status"] == "completed"

    duplicate = await dispatcher.dispatch(
        {
            "type": "workflow_decision_resolve",
            "request_id": "resolve-2",
            "payload": {
                "decision_id": decision.decision_id,
                "nonce": decision.nonce,
                "expected_version": decision.version,
                "response": {"approved": True},
            },
        }
    )
    assert duplicate["ok"] is False
    assert duplicate["error"]["code"] == "decision_already_consumed"
    assert runner.resume.await_count == 1


@pytest.mark.asyncio
async def test_decision_resolve_returns_accepted_async_before_resume_finishes(tmp_path):
    store, runner, run_id, waiting = await _waiting_run(tmp_path)
    assert waiting.status is WorkflowRunStatus.WAITING
    human_store = HumanDecisionStore(store.path)
    decision = (await human_store.list_open_decisions(run_id=run_id))[0]

    async def schedule_resume(scheduled_run_id, responses):
        assert scheduled_run_id == run_id
        assert list(responses) == [decision.interrupt_id]
        # Scheduling is deliberately fast; graph execution belongs to the
        # launcher's tracked task and is not awaited by the IPC request.
        return {
            "accepted": True,
            "created": True,
            "completion_semantics": "accepted_async",
            "run_id": run_id,
        }

    service = WorkflowService(store, runner, human_store=human_store)
    service.launcher = SimpleNamespace(schedule_resume=AsyncMock(side_effect=schedule_resume))
    response = await asyncio.wait_for(
        WorkflowIPCDispatcher(service).dispatch(
            {
                "type": "workflow_decision_resolve",
                "request_id": "resolve-accepted",
                "payload": {
                    "decision_id": decision.decision_id,
                    "nonce": decision.nonce,
                    "expected_version": decision.version,
                    "response": "approve",
                },
            }
        ),
        timeout=0.1,
    )

    assert response["ok"] is True
    resume = response["payload"]["resume"]
    assert resume["accepted"] is True
    assert resume["completion_semantics"] == "accepted_async"
    assert "result" not in resume
    service.launcher.schedule_resume.assert_awaited_once()
    persisted = await store.get_run(run_id)
    assert persisted is not None and persisted["status"] == "retryable"


@pytest.mark.asyncio
async def test_expired_graph_decision_never_calls_resume(tmp_path):
    store, runner, run_id, waiting = await _waiting_run(tmp_path)
    assert waiting.status is WorkflowRunStatus.WAITING
    human_store = HumanDecisionStore(store.path)
    decision = (await human_store.list_open_decisions(run_id=run_id))[0]

    db = await store._connect()
    try:
        await db.execute(
            "UPDATE workflow_decisions SET expires_at=? WHERE decision_id=?",
            (time.time() - 1, decision.decision_id),
        )
        await db.commit()
    finally:
        await db.close()

    runner.resume = AsyncMock(side_effect=runner.resume)
    service = WorkflowService(store, runner, human_store=human_store)
    with pytest.raises(HumanDecisionError) as expired:
        await service.resolve_decision(
            decision.decision_id,
            nonce=decision.nonce,
            expected_version=decision.version,
            response={"approved": True},
        )

    assert expired.value.code == "decision_expired"
    assert runner.resume.await_count == 0
    blocked = await store.get_run(run_id)
    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert blocked["recovery_action"] == "reopen_decision_or_cancel"
