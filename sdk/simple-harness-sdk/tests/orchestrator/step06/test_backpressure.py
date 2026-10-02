# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 6 · S6-02 (D6-2 / D6-3): a slowed Verifier lets results pile up; at the high
watermark the scheduler raises backpressure — no new expansion of formula-tier Tasks,
reduced Worker concurrency, smaller reservations, no new Tasks from the Manager — and
only once the queue has drained to the low watermark is it cleared and work resumes."""

from __future__ import annotations


from agent_orchestrator.contracts import (
    Attempt,
    AttemptStatus,
    Budget,
    Task,
    TaskStatus,
)
from agent_orchestrator.scheduling.allocator import allocate
from agent_orchestrator.scheduling.backpressure import RAISED, BackpressureState


# ------------------------------------------------------------------ D6-3 unit: the gate
def _task(tid, *, priority=1.0, kind="work", ready_at=0.0):
    return Task(
        id=tid,
        mission_id="m",
        parent_task_ids=(),
        dependency_ids=(),
        goal=f"goal {tid}",
        rationale="r",
        success_criteria=("file:x.md",),
        verification_policy=("format_check",),
        allowed_tools=("workspace_list",),
        budget=Budget(max_tokens=1000, max_attempts=3),
        priority=priority,
        status=TaskStatus.READY,
        version=1,
        root_goal="root",
        created_at=0.0,
        kind=kind,
        ready_at=ready_at,
    )


def _attempt(task_id, ordinal, status=AttemptStatus.RETRY_WAIT):
    return Attempt(
        id=f"{task_id}:attempt-{ordinal}",
        task_id=task_id,
        mission_id="m",
        role="worker",
        model="x",
        prompt_version="w",
        context_version="c",
        budget_reserved=Budget(),
        lease_owner=None,
        lease_expires_at=None,
        status=status,
        retry_of=None,
        created_at=0.0,
        version=1,
        ordinal=ordinal,
        creation_key="k",
        input_id="i",
    )


def test_under_pressure_only_starving_tasks_expand_plus_one_exploration_slot():
    raised = BackpressureState(level=RAISED, raised={"pending_verifications": {}}, since=1.0)
    tasks = [
        _task("m:task-1", priority=5.0, ready_at=100.0),  # formula tier, already tried
        _task("m:task-2", priority=4.0, ready_at=100.0),  # formula tier, never tried → exploration
        _task("m:task-3", priority=3.0, ready_at=100.0),  # formula tier, never tried → waits
        _task("m:task-4", priority=0.5, ready_at=0.0),  # starving (waited a whole window)
    ]
    attempts = [_attempt("m:task-1", 1)]
    plan = allocate(
        tasks, attempts, concurrency_limit=8, now=350.0, aging_window_seconds=300.0, pressure=raised
    )
    assert plan.concurrency_limit == 4 and plan.pressure == "RAISED"  # halved
    assert [t.id for t, _ in plan.grants] == ["m:task-4", "m:task-2"]
    calm = allocate(tasks, attempts, concurrency_limit=8, now=350.0, aging_window_seconds=300.0)
    assert len(calm.grants) == 4 and calm.pressure is None
    none = allocate(
        tasks,
        attempts,
        concurrency_limit=8,
        now=350.0,
        aging_window_seconds=300.0,
        pressure=raised,
        exploration_slots=0,
    )
    assert [t.id for t, _ in none.grants] == ["m:task-4"]

