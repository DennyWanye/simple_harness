# SPDX-License-Identifier: Apache-2.0
"""A compound planning subject and a registered method for it, on the assured fixture.

The METHOD_PLAN review is about "a method proposed for this goal".  The assured fixture
Mission has one primitive root, so the tests that need a goal to plan for add one the
way plan admission would: a compound Task with its semantic binding, covering the
fixture's requirement criterion, and a method admitted through the real registry.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SDK_ROOT = Path(__file__).resolve().parents[4]
for extra in (SDK_ROOT / "scripts/assurance_seams",
              SDK_ROOT / "tests/orchestrator/full_target/fixtures/htn"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from htn_world import method, param, step, task_binding  # noqa: E402

from agent_orchestrator.assurance.refs import Pin  # noqa: E402
from agent_orchestrator.contracts import Budget, Task  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.state_machines import TaskStatus  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402

SUBJECT_TASK = "task-seam-plan"
METHOD_ACCEPT = {"schema_version": 2, "verdict": "ACCEPT", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "PASS", "evidence_ids": [],
     "reason": "fixture method review", "limitations": []}], "findings": []}
METHOD_REWORK = {"schema_version": 2, "verdict": "REWORK", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "FAIL", "evidence_ids": [],
     "reason": "the method has no step that writes the report",
     "limitations": ["no step produces the report"]}], "findings": []}


def seed_planning_subject(rt: Any, *, method_id: str = "seam.method") -> tuple[str, Pin, Any]:
    """Register the compound subject (once) and admit ``method_id`` for it."""

    store, mission, world = rt.store, rt.mission, rt.world
    htn = HtnStore(store)
    if htn.task_semantics_of(mission.id, SUBJECT_TASK) is None:
        world.env.register_type("seam.goal", form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                                criteria=("criterion-report",), domain="plan")
        planning = task_binding(world.env, "seam.goal", task_id=SUBJECT_TASK, obligation="obl-root",
                                parameters={"subject": "alpha"})
        with store.transaction():
            htn.put_task_semantics(mission.id, planning)
            store.insert_task(Task(
                id=SUBJECT_TASK, mission_id=mission.id, parent_task_ids=(), dependency_ids=(),
                goal="seam compound planning subject", rationale="fixture planning subject",
                success_criteria=("criterion-report",), verification_policy=("critic_review",),
                allowed_tools=(), budget=Budget(max_tokens=10_000), priority=1.0,
                status=TaskStatus.READY, version=1, root_goal=mission.goal,
                created_at=store.now, ready_at=store.now), ordinal=2)
    contract = method(method_id, "seam.goal", parameter_schema="seam.goal.params",
                      steps=(step("leaf", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                                  capabilities=("plan.read",)),),
                      links=(("criterion-report", "leaf", None),), finalizer="leaf")
    receipt = world.env.admit(contract)
    assert receipt.admitted, receipt.problems
    htn.register_method(contract, world.env.registry.registration(contract.method_ref()))
    ref = contract.method_ref()
    return SUBJECT_TASK, Pin(ref.method_id, int(ref.version), ref.content_hash), contract
