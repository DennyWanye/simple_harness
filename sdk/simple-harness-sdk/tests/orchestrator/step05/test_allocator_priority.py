# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 5 · slice C (D5-8'): the §29.3 starting formula with its weights verbatim and this
build's input scales, eligibility before scoring, conflict Tasks first, a starving Task
promoted after a whole aging window (S5-09)."""

from __future__ import annotations

import pytest
from graph_helpers import complete, graph_service, node

from agent_orchestrator.contracts import (
    Attempt,
    AttemptStatus,
    Budget,
    Task,
    TaskStatus,
)
from agent_orchestrator.scheduling.allocator import (
    ALLOCATOR_VERSION,
    WEIGHTS,
    allocate,
    score_tasks,
)


def _task(
    tid,
    *,
    priority=1.0,
    deps=(),
    status=TaskStatus.READY,
    ready_at=None,
    tokens=10_000,
    kind="work",
    goal=None,
):
    return Task(
        id=tid,
        mission_id="m",
        parent_task_ids=(),
        dependency_ids=tuple(deps),
        goal=goal or f"任务 {tid}",
        rationale="r",
        success_criteria=("file:x",),
        verification_policy=("format_check",),
        allowed_tools=(),
        budget=Budget(max_tokens=tokens, max_attempts=3),
        priority=priority,
        status=status,
        version=1,
        ready_at=ready_at,
        kind=kind,
    )


def _attempt(task_id, ordinal, status, reason=None):
    return Attempt(
        id=f"{task_id}:attempt-{ordinal}",
        task_id=task_id,
        mission_id="m",
        role="worker",
        model="x",
        prompt_version="v",
        context_version="c",
        budget_reserved=Budget(max_tokens=1),
        lease_owner=None,
        lease_expires_at=None,
        status=status,
        retry_of=None,
        created_at=1.0,
        version=1,
        ordinal=ordinal,
        creation_key="k",
        input_id="i",
        failure=None if reason is None else {"reason": reason},
    )


def test_weights_are_the_original_formula_and_scores_are_deterministic():
    assert WEIGHTS == {
        "mission_importance": 0.30,
        "unlock_value": 0.20,
        "progress_signal": 0.15,
        "uncertainty": 0.15,
        "waiting_age": 0.10,
        "estimated_cost": -0.05,
        "duplication_score": -0.05,
    }
    tasks = [
        _task("m:task-1", priority=4.0, ready_at=100.0),
        _task("m:task-2", priority=2.0, deps=("m:task-1",), status=TaskStatus.BLOCKED),
        _task(
            "m:task-3",
            priority=2.0,
            deps=("m:task-1",),
            status=TaskStatus.BLOCKED,
            goal="任务 m:task-1",
        ),  # duplicate goal text
    ]
    attempts = [
        _attempt("m:task-1", 1, AttemptStatus.RETRY_WAIT, "verification_failed"),
        _attempt("m:task-1", 2, AttemptStatus.RETRY_WAIT, "outcome_no_progress"),
    ]
    scores = score_tasks(
        tasks, attempts, now=400.0, aging_window_seconds=300.0, mission_max_tokens=100_000
    )
    one = scores["m:task-1"]
    assert one.version == ALLOCATOR_VERSION and one.tier == 1  # waited a whole window
    assert one.parts == {
        "mission_importance": 1.0,
        "unlock_value": 2 / 3,
        "progress_signal": 0.0,
        "uncertainty": 0.25,
        "waiting_age": 1.0,
        "estimated_cost": 0.1,
        "duplication_score": 1 / 3,
    }
    assert one.score == pytest.approx(
        0.30 + 0.20 * 2 / 3 + 0.15 * 0.25 + 0.10 - 0.05 * 0.1 - 0.05 / 3
    )
    assert (
        score_tasks(
            tasks, attempts, now=400.0, aging_window_seconds=300.0, mission_max_tokens=100_000
        )["m:task-1"]
        == one
    )


def test_eligibility_precedes_scoring():
    tasks = [
        _task(
            "m:task-1", priority=9.0, status=TaskStatus.BLOCKED, deps=("m:task-9",)
        ),  # not eligible: dependency missing
        _task("m:task-2", priority=0.5, ready_at=0.0),
        _task("m:task-3", priority=5.0, ready_at=0.0),
        _task("m:task-9", priority=1.0, status=TaskStatus.COMPLETED),
    ]
    tasks[1] = Task.from_json({**tasks[1].to_json(), "paused": True})
    plan = allocate(tasks, [], concurrency_limit=1, now=10.0)
    assert [t.id for t, _ in plan.grants] == ["m:task-3"]
    plan = allocate(tasks, [], concurrency_limit=3, now=10.0)
    assert [t.id for t, _ in plan.grants] == [
        "m:task-3"
    ]  # paused task-2 and BLOCKED task-1 are not eligible
    assert plan.to_json()["grants"][0]["allocator_version"] == ALLOCATOR_VERSION


# ------------------------------------------------------------------ S5-09
def test_s5_09_a_low_priority_task_is_promoted_after_waiting_a_whole_window():
    low = _task("m:task-1", priority=0.2, ready_at=0.0)
    fresh_high = [_task(f"m:task-{n}", priority=5.0, ready_at=290.0) for n in (2, 3, 4)]
    before = allocate(
        [low, *fresh_high], [], concurrency_limit=1, now=250.0, aging_window_seconds=300.0
    )
    assert [t.id for t, _ in before.grants] == [
        "m:task-2"
    ]  # high priority wins while the wait is short
    after = allocate(
        [low, *fresh_high], [], concurrency_limit=1, now=301.0, aging_window_seconds=300.0
    )
    assert [t.id for t, _ in after.grants] == ["m:task-1"]  # a whole window waited → promoted
    assert (
        after.scores["m:task-1"].tier == 1 and after.scores["m:task-1"].parts["waiting_age"] == 1.0
    )


def test_graph_helpers_keep_ready_at(tmp_path):
    service, mission, t = graph_service(tmp_path, nodes=[node("A"), node("B", ["A"])])
    assert t["A"].ready_at is not None and t["B"].ready_at is None
    complete(service, t["A"])
    assert service.store.get_task(t["B"].id).ready_at is not None
