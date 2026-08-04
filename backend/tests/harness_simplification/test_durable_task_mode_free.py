from __future__ import annotations

import pytest

from deskpet.workflows import WorkflowRunStatus
from deskpet.workflows.definitions.code_nodes import TaskSessionRefV1
from deskpet.workflows.definitions.v1 import (
    durable_task_initial_state,
    register_v1_workflows,
)
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore
from tests.test_workflow_code_graph import (
    FakeEvaluator,
    FakeProposer,
    JournaledDispatch,
    _capability,
    _context,
    _outcome,
    _prepared,
)


@pytest.mark.asyncio
async def test_new_durable_task_never_invokes_legacy_semantic_router(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_route(*args, **kwargs):
        del args, kwargs
        raise AssertionError("new durable tasks must not call route_task")

    monkeypatch.setattr(
        "deskpet.workflows.routing.route_task",
        forbidden_route,
    )
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database)
    saver = FencedAsyncSqliteSaver(database)
    registry = WorkflowRegistry()
    register_v1_workflows(registry)
    runner = WorkflowRunner(
        store,
        saver,
        registry,
        owner="mode-free-durable-task",
    )
    run_id = await runner.start(
        session_id="session-1",
        request_id="request-1",
        turn_id="turn-1",
        workflow_name="durable_task",
        workflow_version="v1",
        capability_snapshot={"tools": [_capability().to_dict()]},
    )
    state = durable_task_initial_state(
        request="Complete the multi-step task",
        run_id=run_id,
        session_ref=TaskSessionRefV1(
            session_id="session-1",
            task_scope_id="task-1",
            delivery_session_id="session-1",
            workspace_hash="workspace-hash",
            session_epoch=1,
            task_epoch=1,
        ),
        capability_snapshot=[_capability()],
        plan_steps=["Perform the action", "Report the verified result"],
        approval_required=False,
    )
    assert "code_session_id" not in state["values"]["session_ref"]
    proposer = FakeProposer(
        [
            _outcome(_prepared("durable-call")),
            _outcome(content="Verified result", stop_reason="end_turn"),
        ]
    )
    result = await runner.run(
        run_id,
        state,
        _context(proposer, JournaledDispatch(), FakeEvaluator()),
    )

    assert result.status is WorkflowRunStatus.COMPLETED
    report = result.output["values"]["delivery_intents"][0]["payload"]
    assert report["status"] == "completed"
