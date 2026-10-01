# SPDX-License-Identifier: Apache-2.0
"""A three-level planning world for the assured loop (HTN 精简 片 B).

Shaped like the desktop deployment: the root goal declares every user requirement, the
sub-goal types declare none (a sub-goal is reviewed on the requirements its parent's
method hands it, under their original ids), and the one step type declares all of them
the way the desktop step types do.
"""
from __future__ import annotations

import json
from typing import Any

from _assured_loop import TOOLS  # noqa: F401  (re-exported for the tests)
from htn_world import Env, method, param, step

from agent_orchestrator.contracts.htn import TaskForm

USER = ("c-user-1", "c-user-2", "c-user-3")
STATEMENTS = ("写出 notes/a.md，列三条要点", "写出 notes/b.md，给出两个例子",
              "写出 README.md，概述前两份并指向它们")
ROOT, PART, DEEPER, WRITE = "sg.goal", "sg.part", "sg.part-deeper", "sg.write"


def env(mission_id: str) -> Env:
    world = Env(mission=mission_id)
    world.register_type(ROOT, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                        criteria=USER, domain="sg", level=0)
    world.register_type(PART, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                        domain="sg", level=1)
    world.register_type(DEEPER, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                        domain="sg", level=2)
    world.register_type(WRITE, parameters=(("subject", "string"),),
                        outputs=(("delivery", "sg.delivery"),), capabilities=("plan.read",),
                        criteria=USER, domain="sg")
    return world


def _write(local_id: str):
    return step(local_id, WRITE, TaskForm.PRIMITIVE, {"subject": param("subject")},
                capabilities=("plan.read",))


def _goal(local_id: str, goal_type: str):
    return step(local_id, goal_type, TaskForm.COMPOUND, {"subject": param("subject")})


def outer(method_id: str = "sg.outer"):
    """root → a sub-goal carrying the first two requirements, then a closing step."""

    return method(method_id, ROOT, parameter_schema=f"{ROOT}.params",
                  steps=(_goal("part", PART), _write("tail")), ordering=(("part", "tail"),),
                  links=(("c-user-1", "part", "c-user-1"), ("c-user-2", "part", "c-user-2"),
                         ("c-user-3", "tail", None)),
                  finalizer="tail")


INNER_LINKS = (("c-user-1", "a", None), ("c-user-2", "b", None))


def inner(method_id: str = "sg.inner", *, links=INNER_LINKS, version: int = 1):
    """the sub-goal → two steps, one requirement each."""

    return method(method_id, PART, parameter_schema=f"{PART}.params", version=version,
                  steps=(_write("a"), _write("b")), ordering=(("a", "b"),), links=links, finalizer="b")


def inner_with_deeper(method_id: str = "sg.inner-deep"):
    """the sub-goal → a deeper sub-goal (first requirement) and one step (second)."""

    return method(method_id, PART, parameter_schema=f"{PART}.params",
                  steps=(_goal("deeper", DEEPER), _write("b")), ordering=(("deeper", "b"),),
                  links=(("c-user-1", "deeper", "c-user-1"), ("c-user-2", "b", None)), finalizer="b")


def deepest(method_id: str = "sg.deepest"):
    return method(method_id, DEEPER, parameter_schema=f"{DEEPER}.params",
                  steps=(_write("only"),), links=(("c-user-1", "only", None),), finalizer="only")


def review_of(*criteria: str, verdict: str = "ACCEPT") -> str:
    grade = "PASS" if verdict == "ACCEPT" else "FAIL"
    return json.dumps({"schema_version": 2, "verdict": verdict, "assessments": [
        {"criterion_id": item, "verdict": grade, "evidence_ids": [], "reason": "fixture method review",
         "limitations": []} for item in criteria], "findings": []})


def open_goal_requests(world: Any) -> list[Any]:
    return [event for event in world.store.list_events(world.mission.id)
            if event.type == "PlanningRepairRequested"
            and (event.payload.get("request") or {}).get("trigger_source") == "GOAL_UNREFINED"]


def plan_revision(world: Any) -> int:
    active = world.htn.active_plan_revision(world.mission.id)
    return 0 if active is None else int(active.revision)


def scopes(world: Any) -> dict[str, list[str]]:
    """``<goal type>/<step>`` → the requirements that occurrence answers for."""

    from agent_orchestrator.contracts.operation_completion import PlanRevisionPinV1
    from agent_orchestrator.orchestrator.operation_completion import OperationCompletionReader

    active = world.htn.active_plan_revision(world.mission.id)
    pin = PlanRevisionPinV1(revision=active.revision, snapshot_hash=active.snapshot_hash)
    network = world.loop._new_mode(world.mission).network(world.mission.id)
    reader = OperationCompletionReader(world.store)
    step_of = {str(child.occurrence_id): str(child.slot_key)
               for instance in network.method_instances for child in instance.child_bindings}
    rows: dict[str, list[str]] = {}
    for spec in network.occurrences:
        signature = str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
        scope = reader.read_scope(world.mission.id, pin, str(spec.occurrence_id))
        rows[f"{signature}/{step_of.get(str(spec.occurrence_id), 'root')}"] = sorted(scope.content_criterion_ids)
    return rows
