from __future__ import annotations

import pytest

from deskpet.agent.context_task import (
    AuthorityProjection,
    GoalProjectionAdapter,
    ReceiptProjectionAdapter,
    TaskContextProjector,
    TaskFact,
    WorkflowProjectionAdapter,
)
from deskpet.agent.goal_store import SessionGoal
from deskpet.agent.task_graph import TaskNode
from deskpet.tools.receipt import ToolReceipt


class _ProjectionSource:
    def __init__(self, projection=None, error: Exception | None = None):
        self.projection = projection
        self.error = error

    async def project(self, session_id: str):
        if self.error:
            raise self.error
        return self.projection


class _GoalStore:
    def __init__(self, goal: SessionGoal | None):
        self.goal = goal

    def get(self, session_id: str):
        return self.goal if self.goal and self.goal.session_id == session_id else None


class _TaskGraph:
    def __init__(self, nodes):
        self.nodes = nodes

    async def list(self, goal_id: str):
        return [node for node in self.nodes if node.goal_id == goal_id]


class _ReceiptStore:
    def __init__(self, receipts):
        self.receipts = receipts

    def load_session(self, session_id: str):
        return [item for item in self.receipts if item.session_id == session_id]


def _goal() -> SessionGoal:
    return SessionGoal(
        session_id="s1",
        text="Ship Context OS",
        set_at=1.0,
        goal_id="g1",
        updated_at=3.0,
    )


def _node(task_id: str, status: str, title: str) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        goal_id="g1",
        session_id="s1",
        title=title,
        status=status,
        depends_on=[],
        claimed_by=None,
        result="done" if status == "completed" else None,
        created_at=1.0,
        updated_at=2.0,
    )


@pytest.mark.asyncio
async def test_goal_projection_uses_goal_tasks_without_guessing_completion():
    projector = TaskContextProjector(
        goal_source=GoalProjectionAdapter(
            _GoalStore(_goal()),
            _TaskGraph(
                [
                    _node("done", "completed", "Implemented"),
                    _node("run", "running", "Verify"),
                    _node("blocked", "blocked", "Needs decision"),
                ]
            ),
        )
    )

    snapshot = await projector.project(effective_sid="s1")

    assert snapshot is not None
    assert snapshot.task_scope_id == "goal:g1"
    assert [item.fact_id for item in snapshot.completed] == ["done"]
    assert [item.fact_id for item in snapshot.pending] == ["run"]
    assert [item.fact_id for item in snapshot.blockers] == ["blocked"]
    assert snapshot.to_protected_fragment().protected is True


@pytest.mark.asyncio
async def test_pending_goal_task_result_projects_as_decision_and_is_remounted():
    node = TaskNode(
        task_id="pending",
        goal_id="g1",
        session_id="s1",
        title="Keep pending",
        status="pending",
        depends_on=[],
        claimed_by=None,
        result="[decision] DECISION-A-0713: Markdown format",
        created_at=1.0,
        updated_at=2.0,
    )
    projector = TaskContextProjector(
        goal_source=GoalProjectionAdapter(
            _GoalStore(_goal()),
            _TaskGraph([node]),
        )
    )

    snapshot = await projector.project(effective_sid="s1")

    assert snapshot is not None
    assert [item.fact_id for item in snapshot.decisions] == ["pending:result"]
    assert snapshot.decisions[0].text == "DECISION-A-0713: Markdown format"
    assert "DECISION-A-0713" in snapshot.to_protected_fragment().content


@pytest.mark.asyncio
async def test_untyped_pending_goal_task_result_is_not_a_decision():
    node = TaskNode(
        task_id="progress",
        goal_id="g1",
        session_id="s1",
        title="Keep working",
        status="running",
        depends_on=[],
        claimed_by=None,
        result="draft is 50 percent complete",
        created_at=1.0,
        updated_at=2.0,
    )
    projector = TaskContextProjector(
        goal_source=GoalProjectionAdapter(_GoalStore(_goal()), _TaskGraph([node]))
    )

    snapshot = await projector.project(effective_sid="s1")

    assert snapshot is not None
    assert snapshot.decisions == ()
    assert snapshot.pending[0].result == "draft is 50 percent complete"


@pytest.mark.asyncio
async def test_workflow_objective_wins_and_conflict_is_explicit():
    workflow = AuthorityProjection(
        authority="workflow",
        entity_id="run-1",
        revision="7",
        objective="Render the approved deck",
        pending=(TaskFact("step-2", "Render", "workflow", "running"),),
    )
    goal = AuthorityProjection(
        authority="goal",
        entity_id="goal-1",
        revision="2",
        objective="Make a deck",
        pending=(TaskFact("goal-task", "Draft", "goal_task", "pending"),),
    )
    projector = TaskContextProjector(
        workflow_source=_ProjectionSource(workflow),
        goal_source=_ProjectionSource(goal),
    )

    snapshot = await projector.project(effective_sid="s1")

    assert snapshot is not None
    assert snapshot.task_scope_id == "workflow:run-1"
    assert snapshot.objective == "Render the approved deck"
    assert snapshot.conflicts[0].field == "objective"


@pytest.mark.asyncio
async def test_accepted_receipt_remains_pending_and_success_is_completed():
    accepted = ToolReceipt(
        receipt_id="r1",
        tool_name="ppt_create",
        args_hash="a",
        started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T00:00:01Z",
        duration_ms=1,
        ok=False,
        session_id="s1",
        phase="accepted",
        outcome="pending",
    )
    success = ToolReceipt(
        receipt_id="r2",
        tool_name="ppt_create",
        args_hash="b",
        started_at="2026-01-01T00:00:01Z",
        ended_at="2026-01-01T00:00:02Z",
        duration_ms=1,
        ok=True,
        artifacts=["sha-1"],
        session_id="s1",
        phase="delivered",
        outcome="success",
    )
    projector = TaskContextProjector(
        receipt_source=ReceiptProjectionAdapter(_ReceiptStore([accepted, success]))
    )

    snapshot = await projector.project(
        effective_sid="s1",
        request_id="request-1",
        user_text="Create the deck",
    )

    assert snapshot is not None
    assert snapshot.task_scope_id == "session:s1"
    assert [item.fact_id for item in snapshot.pending] == ["receipt:r1"]
    assert [item.fact_id for item in snapshot.completed] == ["receipt:r2"]
    assert snapshot.artifacts[0].artifact_id == "sha-1"


@pytest.mark.asyncio
async def test_idle_chat_has_no_snapshot_and_optional_source_failure_safe_fails():
    projector = TaskContextProjector(
        workflow_source=_ProjectionSource(error=RuntimeError("offline"))
    )
    assert await projector.project(effective_sid="s1", user_text="hello") is None

    structured = TaskFact("todo-1", "Write tests", "typed_todo", "pending")
    snapshot = await projector.project(
        effective_sid="s1",
        request_id="request-1",
        user_text="Implement it",
        structured_evidence=[structured],
    )
    assert snapshot is not None
    assert snapshot.source_errors == ("workflow:RuntimeError",)
    assert snapshot.pending == (structured,)


@pytest.mark.asyncio
async def test_independent_roots_keep_distinct_context_task_scopes():
    projector = TaskContextProjector()
    evidence = [TaskFact("todo", "Run task", "session_request", "pending")]

    first = await projector.project(
        effective_sid="default",
        request_id="request-a",
        task_scope_id="task:root-a",
        user_text="First task",
        structured_evidence=evidence,
    )
    second = await projector.project(
        effective_sid="default",
        request_id="request-b",
        task_scope_id="task:root-b",
        user_text="Second task",
        structured_evidence=evidence,
    )

    assert first is not None and second is not None
    assert first.task_scope_id == "task:root-a"
    assert second.task_scope_id == "task:root-b"


@pytest.mark.asyncio
async def test_projection_is_deterministic_across_source_fact_order():
    facts = (
        TaskFact("b", "Second", "goal_task", "pending"),
        TaskFact("a", "First", "goal_task", "pending"),
    )
    left = TaskContextProjector(
        goal_source=_ProjectionSource(
            AuthorityProjection("goal", "g1", "1", "Goal", pending=facts)
        )
    )
    right = TaskContextProjector(
        goal_source=_ProjectionSource(
            AuthorityProjection("goal", "g1", "1", "Goal", pending=tuple(reversed(facts)))
        )
    )

    a = await left.project(effective_sid="s1")
    b = await right.project(effective_sid="s1")
    assert a is not None and b is not None
    assert a.to_store_record() == b.to_store_record()


@pytest.mark.asyncio
async def test_workflow_adapter_projects_active_nodes_and_decision_read_only():
    class _WorkflowService:
        def __init__(self):
            self.calls = []

        async def list_runs(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "runs": [
                    {
                        "run_id": "run-1",
                        "workflow_name": "deep_research",
                        "status": "running",
                        "active_nodes": ["collect", "synthesize"],
                        "recovery_action": "resume",
                        "run_version": 3,
                        "event_seq": 7,
                        "updated_at": 10.0,
                    }
                ]
            }

    service = _WorkflowService()
    projector = TaskContextProjector(
        workflow_source=WorkflowProjectionAdapter(service)
    )

    snapshot = await projector.project(effective_sid="s1")

    assert snapshot is not None
    assert snapshot.task_scope_id == "workflow:run-1"
    assert [fact.text for fact in snapshot.pending] == ["collect", "synthesize"]
    assert snapshot.decisions[0].text == "resume"
    assert service.calls == [{"session_id": "s1", "limit": 20}]
